#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# remove-node preflight: capacity / spread / local-data / last-capable-node.
# es_auto_installer.sh runs it on the controller, against the API server; it
# is not copied to a node.
#
# A heuristic, not the kube-scheduler: it models node selectors, required node
# affinity, taints, required pod anti-affinity, topologySpreadConstraints
# (DoNotSchedule) and node-bound volumes. Capacity is a greedy best-fit
# bin-pack, most constrained pod first, so it can refuse a removal another
# order would fit: a false refusal costs a retry, a false pass an outage.
#
# Usage: simulate_node_removal.py [--plan] <node1> [<node2> ...]
# Exit 0, silent: nothing found. Exit 1: one finding per line
# ("<check>: <message>"). Exit 2, reason on stderr: the simulation could not
# run, which the caller treats as a refusal. --plan first prints the pod count
# by namespace and the target node of every pod placed ("<owner> -> <node>").

import json
import re
import shutil
import subprocess
import sys

# Every node has these; "pods" is the node's pod limit, one per pod.
UNIVERSAL_RESOURCES = ("cpu", "memory", "ephemeral-storage", "pods")


def kubectl_json(*args):
    # Resolve to an absolute path up front so subprocess never does its own
    # PATH lookup on a bare "kubectl" (Bandit B607).
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise FileNotFoundError("kubectl not found in PATH")
    out = subprocess.run(
        [kubectl, "get", *args, "-o", "json"],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)["items"]


_QUANTITY_RE = re.compile(r"^([+-]?(?:[0-9]+(?:[.][0-9]*)?|[.][0-9]+)(?:[eE][+-]?[0-9]+)?)([a-zA-Z]*)$")
_SUFFIX_MULT = {
    "": 1, "m": 1e-3,
    "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
    "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "Pi": 2 ** 50, "Ei": 2 ** 60,
}


def parse_quantity(value):
    """A Kubernetes quantity as a number. One it cannot read raises: counted as 0 it would pass."""
    if value is None:
        return 0.0
    m = _QUANTITY_RE.match(str(value))
    if not m or m.group(2) not in _SUFFIX_MULT:
        raise ValueError(f"cannot read the quantity {value!r}")
    return float(m.group(1)) * _SUFFIX_MULT[m.group(2)]


def format_quantity(resource, value):
    if resource in ("cpu", "pods"):
        return f"{value:g}"
    gi = value / (2 ** 30)
    if gi >= 1:
        return f"{gi:.3g}Gi"
    mi = value / (2 ** 20)
    if mi >= 1:
        return f"{mi:.3g}Mi"
    return f"{value:g}"


def is_workload_pod(pod):
    if pod.get("status", {}).get("phase") in ("Succeeded", "Failed"):
        return False
    # Terminating: its controller has already created a replacement, which is
    # counted where it runs. On a lost node every pod ends up like this, and
    # counting both would double the demand.
    if pod["metadata"].get("deletionTimestamp"):
        return False
    owners = pod["metadata"].get("ownerReferences") or []
    return not any(o.get("kind") in ("DaemonSet", "Node") for o in owners)


def is_replaced(pod):
    """Terminating, and its controller has started a replacement already: all but a StatefulSet's."""
    owners = pod["metadata"].get("ownerReferences") or []
    return bool(pod["metadata"].get("deletionTimestamp")) and not any(o.get("kind") == "StatefulSet" for o in owners)


def owner_label(pod):
    ns = pod["metadata"]["namespace"]
    owners = pod["metadata"].get("ownerReferences") or []
    name = owners[0].get("name", pod["metadata"]["name"]) if owners else pod["metadata"]["name"]
    return f"{ns}/{name}"


def pod_requests(pod):
    """What the scheduler reserves: the containers plus the sidecars, or an init
    container plus the sidecars started before it, whichever is larger."""
    def requests(container):
        return {r: parse_quantity(v) for r, v in ((container.get("resources") or {}).get("requests") or {}).items()}

    sidecars, init_peak = {}, {}
    for c in pod["spec"].get("initContainers") or []:
        for r, v in requests(c).items():
            if c.get("restartPolicy") == "Always":
                sidecars[r] = sidecars.get(r, 0.0) + v
            else:
                init_peak[r] = max(init_peak.get(r, 0.0), v + sidecars.get(r, 0.0))
    totals = {**sidecars, "pods": 1.0}
    for c in pod["spec"].get("containers") or []:
        for r, v in requests(c).items():
            totals[r] = totals.get(r, 0.0) + v
    return {r: max(totals.get(r, 0.0), init_peak.get(r, 0.0)) for r in set(totals) | set(init_peak)}


# ---------------------------------------------------------------------------
# Label/selector + affinity/taint matching — shared between node-affinity
# (matched against node labels) and pod anti-affinity / topology spread
# (matched against pod labels): both are LabelSelector-shaped.
# ---------------------------------------------------------------------------

def selector_matches(labels, selector):
    for k, v in (selector.get("matchLabels") or {}).items():
        if labels.get(k) != v:
            return False
    for expr in selector.get("matchExpressions") or []:
        key, op, vals = expr.get("key"), expr.get("operator"), expr.get("values") or []
        actual = labels.get(key)
        if op == "In":
            if actual not in vals:
                return False
        elif op == "NotIn":
            if actual in vals:
                return False
        elif op == "Exists":
            if key not in labels:
                return False
        elif op == "DoesNotExist":
            if key in labels:
                return False
        elif op in ("Gt", "Lt"):
            try:
                a, b = float(actual), float(vals[0])
            except (TypeError, ValueError, IndexError):
                return False
            if op == "Gt" and not (a > b):
                return False
            if op == "Lt" and not (a < b):
                return False
        else:
            return False
    return True


def node_matches_required_affinity(name, labels, node_affinity):
    terms = (node_affinity.get("requiredDuringSchedulingIgnoredDuringExecution") or {}).get("nodeSelectorTerms") or []
    if not terms:
        return True
    # matchFields can only name the node (metadata.name).
    return any(selector_matches(labels, {"matchExpressions": t.get("matchExpressions") or []})
               and selector_matches({"metadata.name": name}, {"matchExpressions": t.get("matchFields") or []})
               for t in terms)


def toleration_matches(toleration, taint):
    if toleration.get("effect") and toleration["effect"] != taint.get("effect"):
        return False
    if toleration.get("operator", "Equal") == "Exists":
        return not toleration.get("key") or toleration.get("key") == taint.get("key")
    return toleration.get("key") == taint.get("key") and (toleration.get("value") or "") == (taint.get("value") or "")


def node_tolerates_taints(taints, tolerations):
    for taint in taints:
        if taint.get("effect") not in ("NoSchedule", "NoExecute"):
            continue
        if not any(toleration_matches(t, taint) for t in tolerations):
            return False
    return True


# ---------------------------------------------------------------------------
# Local-data detection
# ---------------------------------------------------------------------------

_LOCAL_PROVISIONERS = ("rancher.io/local-path", "openebs.io/local")


def is_local_pv(pv):
    """A `local` PV, or one a known local-volume provisioner made."""
    if "local" in pv.get("spec", {}):
        return True
    provisioner = (pv.get("metadata", {}).get("annotations") or {}).get("pv.kubernetes.io/provisioned-by", "")
    return any(p in provisioner for p in _LOCAL_PROVISIONERS)


def main():
    argv = sys.argv[1:]
    show_plan = "--plan" in argv
    target_nodes = set(a for a in argv if a != "--plan")
    if not target_nodes:
        print("simulate_node_removal.py: no target node(s) given", file=sys.stderr)
        return 2

    nodes = kubectl_json("nodes")
    pods = kubectl_json("pods", "-A")
    pvcs = kubectl_json("pvc", "-A")
    pvs = kubectl_json("pv")

    node_by_name = {n["metadata"]["name"]: n for n in nodes}
    remaining_names = [n for n in node_by_name if n not in target_nodes]
    node_labels = {n: node_by_name[n]["metadata"].get("labels") or {} for n in remaining_names}
    node_taints = {n: node_by_name[n].get("spec", {}).get("taints") or [] for n in remaining_names}
    allocatable = {
        n: {r: parse_quantity(v) for r, v in (node_by_name[n].get("status", {}).get("allocatable") or {}).items()}
        for n in remaining_names
    }

    usage = {n: {} for n in remaining_names}
    for p in pods:
        nn = p.get("spec", {}).get("nodeName")
        if nn in usage and p.get("status", {}).get("phase") in ("Running", "Pending"):
            for r, v in pod_requests(p).items():
                usage[nn][r] = usage[nn].get(r, 0.0) + v
    free = {n: {r: allocatable[n].get(r, 0.0) - usage[n].get(r, 0.0) for r in allocatable[n]} for n in remaining_names}

    evicted = [p for p in pods if p.get("spec", {}).get("nodeName") in target_nodes and is_workload_pod(p)
               and not is_replaced(p)]

    def capability_nodes(pod):
        sel = pod["spec"].get("nodeSelector") or {}
        aff = ((pod["spec"].get("affinity") or {}).get("nodeAffinity")) or {}
        tol = pod["spec"].get("tolerations") or []
        ext = [r for r in pod_requests(pod) if r not in UNIVERSAL_RESOURCES]
        out = []
        for n in remaining_names:
            labels = node_labels[n]
            if any(labels.get(k) != v for k, v in sel.items()):
                continue
            if not node_matches_required_affinity(n, labels, aff):
                continue
            if not node_tolerates_taints(node_taints[n], tol):
                continue
            if any(r not in allocatable[n] for r in ext):
                continue
            out.append(n)
        return out

    findings = []
    eligible = [(pod, owner_label(pod), capability_nodes(pod)) for pod in evicted]

    # --- last-capable-node ---------------------------------------------------
    # A pod no remaining node can take is reported here and left out of the bin-pack.
    placeable = []
    for pod, owner, caps in eligible:
        reqs = pod_requests(pod)
        if not caps:
            ext = [r for r in reqs if r not in UNIVERSAL_RESOURCES]
            reason = f"requests {', '.join(sorted(ext))}" if ext else "its nodeSelector/affinity/taint requirements"
            findings.append(("last-capable-node", owner,
                              f"no remaining node can satisfy {reason}"))
            continue
        placeable.append((pod, owner, caps, reqs))

    # --- capacity: greedy best-fit bin-pack ----------------------------------
    # Fewest eligible nodes first, then the largest request (relative to the
    # largest allocatable among them, so cpu, memory and GPUs compare).
    def _normalized_demand(caps, reqs):
        total = 0.0
        for r, v in reqs.items():
            cap = max((allocatable[n].get(r, 0.0) for n in caps), default=0.0)
            if cap > 0:
                total += v / cap
        return total

    placeable.sort(key=lambda item: (len(item[2]), -_normalized_demand(item[2], item[3])))

    placements = []
    working_free = {n: dict(free[n]) for n in remaining_names}
    for pod, owner, caps, reqs in placeable:
        def _fits(n):
            return all(working_free[n].get(r, 0.0) >= v for r, v in reqs.items())

        candidates = [n for n in caps if _fits(n)]
        if not candidates:
            # Name the closest eligible node (smallest normalized shortfall)
            # rather than just "nothing fits" — that's the one an operator
            # would free up first.
            def _deficit(n):
                return sum(max(0.0, v - working_free[n].get(r, 0.0)) / v
                           for r, v in reqs.items() if v > 0)

            closest = min(caps, key=_deficit)
            short = {r: v - working_free[closest].get(r, 0.0) for r, v in reqs.items()
                     if working_free[closest].get(r, 0.0) < v}
            detail = ", ".join(f"{format_quantity(r, v)} more {r}" for r, v in short.items())
            findings.append(("capacity", owner,
                              f"does not fit on any remaining eligible node — closest is {closest} "
                              f"(short {detail})"))
            continue

        def _slack_after(n):
            return sum((working_free[n].get(r, 0.0) - v) / allocatable[n][r]
                       for r, v in reqs.items() if allocatable[n].get(r, 0.0) > 0)

        best = min(candidates, key=_slack_after)
        placements.append((owner, best))
        for r, v in reqs.items():
            working_free[best][r] = working_free[best].get(r, 0.0) - v

    # --- spread: required pod anti-affinity --------------------------------
    for pod, owner, caps in eligible:
        terms = (((pod["spec"].get("affinity") or {}).get("podAntiAffinity")) or {}) \
            .get("requiredDuringSchedulingIgnoredDuringExecution") or []
        for term in terms:
            tk = term.get("topologyKey")
            if not tk:
                continue
            domains = {node_labels[n][tk] for n in caps if tk in node_labels[n]}
            ns_list = term.get("namespaces") or [pod["metadata"]["namespace"]]
            sel = term.get("labelSelector") or {}
            matching = [p for p in pods
                        if is_workload_pod(p) and not is_replaced(p)
                        and p["metadata"]["namespace"] in ns_list
                        and selector_matches(p["metadata"].get("labels") or {}, sel)]
            if selector_matches(pod["metadata"].get("labels") or {}, sel):
                # The pods avoid each other: each needs its own domain.
                if len(matching) > len(domains):
                    findings.append(("spread", owner,
                                      f"requires {len(matching)} distinct {tk} value(s) (required pod anti-affinity), "
                                      f"only {len(domains)} remain among eligible nodes"))
            else:
                # It avoids other pods: it needs one domain where none of them runs.
                taken = {node_labels[p["spec"]["nodeName"]].get(tk) for p in matching
                         if p.get("spec", {}).get("nodeName") in node_labels}
                if not domains - taken:
                    findings.append(("spread", owner,
                                      f"every eligible {tk} runs a pod it must avoid (required pod anti-affinity)"))

    # --- spread: topologySpreadConstraints (DoNotSchedule only) ------------
    # The scheduler places a pod in its least-loaded eligible domain, where the
    # skew is at most 1: only a constraint with no eligible domain left fails.
    for pod, owner, caps in eligible:
        if not caps:
            continue
        for tsc in pod["spec"].get("topologySpreadConstraints") or []:
            tk = tsc.get("topologyKey")
            if tsc.get("whenUnsatisfiable") == "DoNotSchedule" and not any(tk in node_labels[n] for n in caps):
                findings.append(("spread", owner,
                                  f"topologySpreadConstraint on {tk} has no eligible remaining domain"))

    # --- local-data ----------------------------------------------------------
    pvc_index = {(p["metadata"]["namespace"], p["metadata"]["name"]): p for p in pvcs}
    pv_by_name = {p["metadata"]["name"]: p for p in pvs}
    for pod in evicted:
        owner = owner_label(pod)
        node_name = pod["spec"].get("nodeName")
        for vol in pod["spec"].get("volumes") or []:
            claim = (vol.get("persistentVolumeClaim") or {}).get("claimName")
            if not claim:
                continue
            pvc = pvc_index.get((pod["metadata"]["namespace"], claim))
            if not pvc:
                continue
            pv = pv_by_name.get(pvc.get("spec", {}).get("volumeName"))
            if not pv:
                continue
            if not is_local_pv(pv):
                continue
            findings.append(("local-data", owner,
                              f"uses a node-local volume (pvc {claim}, pv {pv['metadata']['name']}) "
                              f"bound to {node_name} — data does not move with the pod"))

    if show_plan:
        by_ns = {}
        for p in evicted:
            by_ns[p["metadata"]["namespace"]] = by_ns.get(p["metadata"]["namespace"], 0) + 1
        breakdown = ", ".join(f"{ns} {n}" for ns, n in sorted(by_ns.items()))
        print(f"plan: {', '.join(sorted(target_nodes))} runs {len(evicted)} pod(s) ({breakdown})")
        for owner, node in sorted(placements):
            print(f"  {owner} -> {node}")

    for check, workload, detail in dict.fromkeys(findings):
        print(f"{check}: {workload} — {detail}")

    return 1 if findings else 0


def run():
    # An uncaught exception exits 1, the same code as "findings" — so a
    # crash would read as a refusal with no reason given, or, to a caller
    # only checking for findings, as nothing found. Every failure to
    # simulate gets its own exit code instead.
    try:
        return main()
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or "").strip() or str(e)
        print(f"simulate_node_removal.py: kubectl failed: {detail}", file=sys.stderr)
    except Exception as e:  # pylint: disable=broad-except
        print(f"simulate_node_removal.py: error: {type(e).__name__}: {e}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(run())
