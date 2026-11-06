#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# remove-node preflight: capacity / spread / local-data / last-capable-node.
#
# Unlike every other file in roles/kubernetes/files/, this one is never
# copied to a managed node — es_auto_installer.sh (do_remove_node) runs it
# directly on the controller, against the API server, using the KUBECONFIG
# it has already exported. It lives here only because the kubernetes role
# is where it conceptually belongs, next to the add-node/remove-node tasks
# that drive it.
#
# This is a heuristic, not a scheduler simulation: it models node selectors,
# required node affinity (In/NotIn/Exists/DoesNotExist/Gt/Lt), taints vs
# tolerations, required pod anti-affinity, topologySpreadConstraints
# (whenUnsatisfiable: DoNotSchedule), and PV node affinity / known local
# provisioners closely enough to catch the scenarios in docs/customize/
# node_scaling.md, but it is not the kube-scheduler — an exotic combination
# of constraints it doesn't model can still slip through. Capacity is
# checked via a greedy best-fit bin-pack (most-constrained pod first, each
# placement decrementing a working copy of the target node's free capacity
# before the next pod is considered) rather than an aggregate sum — a pod
# whose only eligible node is full is caught even when some other node that
# pod could never run on has plenty of spare room. Not an exact solver
# either: a different greedy order could occasionally find room this one
# does not — intentional, since a false refusal costs a retry and a false
# pass costs an outage.
#
# Usage: simulate_node_removal.py [--plan] <node1> [<node2> ...]
# Exit 0, silent, when nothing is found. Exit 1 with one finding per line
# ("<check>: <message>") when the removal should be refused. --plan also
# prints, ahead of any findings, the pod count by namespace and a target
# node for every pod the bin-pack actually placed ("<owner> -> <node>").

import json
import re
import shutil
import subprocess
import sys

UNIVERSAL_RESOURCES = ("cpu", "memory", "ephemeral-storage")


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


_QUANTITY_RE = re.compile(r"^([+-]?[0-9.eE+-]+)([a-zA-Z]*)$")
_SUFFIX_MULT = {
    "": 1, "m": 1e-3,
    "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9, "T": 1e12, "P": 1e15, "E": 1e18,
    "Ki": 2 ** 10, "Mi": 2 ** 20, "Gi": 2 ** 30, "Ti": 2 ** 40, "Pi": 2 ** 50, "Ei": 2 ** 60,
}


def parse_quantity(value):
    if value is None:
        return 0.0
    m = _QUANTITY_RE.match(str(value))
    if not m:
        return 0.0
    num, suf = m.groups()
    mult = _SUFFIX_MULT.get(suf)
    if mult is None:
        return 0.0
    return float(num) * mult


def format_quantity(resource, value):
    if resource == "cpu":
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
    owners = pod["metadata"].get("ownerReferences") or []
    return not any(o.get("kind") in ("DaemonSet", "Node") for o in owners)


def owner_label(pod):
    ns = pod["metadata"]["namespace"]
    owners = pod["metadata"].get("ownerReferences") or []
    name = owners[0].get("name", pod["metadata"]["name"]) if owners else pod["metadata"]["name"]
    return f"{ns}/{name}"


def pod_requests(pod):
    totals = {}
    containers = (pod["spec"].get("containers") or []) + (pod["spec"].get("initContainers") or [])
    for c in containers:
        for r, v in ((c.get("resources") or {}).get("requests") or {}).items():
            totals[r] = totals.get(r, 0.0) + parse_quantity(v)
    return totals


def requires_capability_check(resource):
    return resource not in UNIVERSAL_RESOURCES


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


def node_matches_required_affinity(labels, node_affinity):
    terms = (node_affinity.get("requiredDuringSchedulingIgnoredDuringExecution") or {}).get("nodeSelectorTerms") or []
    if not terms:
        return True
    return any(selector_matches(labels, {"matchExpressions": t.get("matchExpressions") or []}) for t in terms)


def toleration_matches(toleration, taint):
    if toleration.get("effect") and toleration["effect"] != taint.get("effect"):
        return False
    if toleration.get("operator", "Equal") == "Exists":
        return not toleration.get("key") or toleration.get("key") == taint.get("key")
    return toleration.get("key") == taint.get("key") and toleration.get("value") == taint.get("value")


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

_LOCAL_PROVISIONERS = ("rancher.io/local-path", "openebs.io/local", "kubernetes.io/no-provisioner")


def local_pv_bound_hosts(pv):
    spec = pv.get("spec", {})
    if "local" in spec:
        terms = (spec.get("nodeAffinity", {}).get("required") or {}).get("nodeSelectorTerms") or []
        for term in terms:
            for expr in term.get("matchExpressions") or []:
                if expr.get("key") == "kubernetes.io/hostname" and expr.get("operator") == "In":
                    return expr.get("values") or []
        return []
    provisioner = (pv.get("metadata", {}).get("annotations") or {}).get("pv.kubernetes.io/provisioned-by", "")
    if any(p in provisioner for p in _LOCAL_PROVISIONERS):
        return None  # local by provisioner, but no explicit host list to report
    return False


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

    evicted = [p for p in pods if p.get("spec", {}).get("nodeName") in target_nodes and is_workload_pod(p)]

    def capability_nodes(pod):
        sel = pod["spec"].get("nodeSelector") or {}
        aff = ((pod["spec"].get("affinity") or {}).get("nodeAffinity")) or {}
        tol = pod["spec"].get("tolerations") or []
        ext = [r for r in pod_requests(pod) if requires_capability_check(r)]
        out = []
        for n in remaining_names:
            labels = node_labels[n]
            if any(labels.get(k) != v for k, v in sel.items()):
                continue
            if not node_matches_required_affinity(labels, aff):
                continue
            if not node_tolerates_taints(node_taints[n], tol):
                continue
            if any(r not in allocatable[n] for r in ext):
                continue
            out.append(n)
        return out

    findings = []

    # --- last-capable-node ---------------------------------------------------
    # Pods with zero eligible nodes are an intrinsic mismatch (nodeSelector/
    # affinity/taint/extended-resource), true regardless of load — reported
    # here, and excluded from the capacity bin-pack below since there is
    # nowhere for them to even compete for room.
    placeable = []
    for pod in evicted:
        caps = capability_nodes(pod)
        owner = owner_label(pod)
        reqs = pod_requests(pod)
        if not caps:
            ext = [r for r in reqs if requires_capability_check(r)]
            reason = f"requests {', '.join(sorted(ext))}" if ext else "its nodeSelector/affinity/taint requirements"
            findings.append(("last-capable-node", owner,
                              f"no remaining node can satisfy {reason}"))
            continue
        placeable.append((pod, owner, caps, reqs))

    # --- capacity: greedy best-fit bin-pack ----------------------------------
    # Most-constrained-first: fewest eligible nodes, then largest request
    # (normalized against the best allocatable among its own candidates, so
    # cpu/memory/gpu aren't compared on raw, incomparable scales) — placing
    # the hardest-to-place pods first leaves the most room for flexibility
    # later, same heuristic as classic best-fit-decreasing bin-packing.
    def _normalized_demand(caps, reqs):
        total = 0.0
        for r, v in reqs.items():
            cap = max((allocatable[n].get(r, 0.0) for n in caps), default=0.0)
            if cap > 0:
                total += v / cap
        return total

    placeable.sort(key=lambda item: (len(item[2]), -_normalized_demand(item[2], item[3])))

    placements = []  # (owner, node) for every pod the bin-pack actually placed
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

        # Best-fit: the candidate left with the least normalized slack
        # (leftover free / that node's own allocatable) after taking this
        # pod, so larger-slack nodes stay available for whatever is placed
        # next.
        def _slack_after(n):
            return sum((working_free[n].get(r, 0.0) - v) / allocatable[n][r]
                       for r, v in reqs.items() if allocatable[n].get(r, 0.0) > 0)

        best = min(candidates, key=_slack_after)
        placements.append((owner, best))
        for r, v in reqs.items():
            working_free[best][r] = working_free[best].get(r, 0.0) - v

    # --- spread: required pod anti-affinity --------------------------------
    for pod in evicted:
        owner = owner_label(pod)
        caps = capability_nodes(pod)
        terms = (((pod["spec"].get("affinity") or {}).get("podAntiAffinity")) or {}) \
            .get("requiredDuringSchedulingIgnoredDuringExecution") or []
        for term in terms:
            tk = term.get("topologyKey")
            if not tk:
                continue
            domains = {node_labels[n][tk] for n in caps if tk in node_labels[n]}
            ns_list = term.get("namespaces") or [pod["metadata"]["namespace"]]
            sel = term.get("labelSelector") or {}
            total_needed = sum(
                1 for p in pods
                if is_workload_pod(p)
                and p["metadata"]["namespace"] in ns_list
                and selector_matches(p["metadata"].get("labels") or {}, sel)
            )
            if total_needed > len(domains):
                findings.append(("spread", owner,
                                  f"requires {total_needed} distinct {tk} value(s) (required pod anti-affinity), "
                                  f"only {len(domains)} remain among eligible nodes"))

    # --- spread: topologySpreadConstraints (DoNotSchedule only) ------------
    for pod in evicted:
        owner = owner_label(pod)
        caps = capability_nodes(pod)
        for tsc in pod["spec"].get("topologySpreadConstraints") or []:
            if tsc.get("whenUnsatisfiable") != "DoNotSchedule":
                continue
            tk = tsc.get("topologyKey")
            max_skew = tsc.get("maxSkew", 1)
            sel = tsc.get("labelSelector") or {}
            domains = sorted({node_labels[n][tk] for n in caps if tk in node_labels[n]})
            if not domains:
                findings.append(("spread", owner,
                                  f"topologySpreadConstraint on {tk} has no eligible remaining domain"))
                continue
            ns = pod["metadata"]["namespace"]
            counts = {d: 0 for d in domains}
            for p in pods:
                if not is_workload_pod(p):
                    continue
                nn = p.get("spec", {}).get("nodeName")
                if nn not in node_labels or p["metadata"]["namespace"] != ns:
                    continue
                if not selector_matches(p["metadata"].get("labels") or {}, sel):
                    continue
                dv = node_labels[nn].get(tk)
                if dv in counts:
                    counts[dv] += 1
            best_domain = min(counts, key=counts.get)
            counts[best_domain] += 1
            skew = max(counts.values()) - min(counts.values())
            if skew > max_skew:
                findings.append(("spread", owner,
                                  f"topologySpreadConstraint on {tk} (maxSkew {max_skew}) cannot be met after "
                                  f"removal — best-case skew would be {skew}"))

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
            hosts = local_pv_bound_hosts(pv)
            if hosts is False:
                continue
            findings.append(("local-data", owner,
                              f"uses local-path volume (pvc {claim}, pv {pv['metadata']['name']}) "
                              f"bound to {node_name} — data does not move with the pod"))

    if show_plan:
        by_ns = {}
        for p in evicted:
            by_ns[p["metadata"]["namespace"]] = by_ns.get(p["metadata"]["namespace"], 0) + 1
        breakdown = ", ".join(f"{ns} {n}" for ns, n in sorted(by_ns.items()))
        print(f"plan: {', '.join(sorted(target_nodes))} runs {len(evicted)} pod(s) ({breakdown})")
        # Not deduplicated: two pods under the same owner (e.g. a multi-
        # replica ReplicaSet) can land on different nodes, and collapsing
        # them would silently drop that half of "target node for each pod".
        for owner, node in sorted(placements):
            print(f"  {owner} -> {node}")

    for check, workload, detail in findings:
        print(f"{check}: {workload} — {detail}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
