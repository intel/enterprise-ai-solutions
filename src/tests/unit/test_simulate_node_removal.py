#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Unit tests for roles/kubernetes/files/simulate_node_removal.py, the
# remove-node preflight. No cluster needed: kubectl_json is replaced with a
# fake that serves hand-built Node/Pod/PVC/PV objects.
#
#   python3 -m pytest src/tests/unit

import importlib.util
import pathlib
import subprocess

import pytest

_SCRIPT = (pathlib.Path(__file__).resolve().parents[3]
           / "roles" / "kubernetes" / "files" / "simulate_node_removal.py")
_spec = importlib.util.spec_from_file_location("simulate_node_removal", _SCRIPT)
sim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sim)


# ---------------------------------------------------------------------------
# Object builders
# ---------------------------------------------------------------------------

def node(name, cpu="8", memory="32Gi", labels=None, taints=None, extra=None):
    allocatable = {"cpu": cpu, "memory": memory, "ephemeral-storage": "100Gi", "pods": "110"}
    allocatable.update(extra or {})
    return {
        "metadata": {"name": name, "labels": {"kubernetes.io/hostname": name, **(labels or {})}},
        "spec": {"taints": taints or []},
        "status": {"allocatable": allocatable},
    }


def pod(name, node_name, cpu="1", memory="1Gi", ns="default", owner=("ReplicaSet", "app-rs"),
        labels=None, phase="Running", extra_requests=None, **spec):
    requests = {"cpu": cpu, "memory": memory, **(extra_requests or {})}
    return {
        "metadata": {
            "name": name,
            "namespace": ns,
            "labels": labels or {},
            "ownerReferences": [{"kind": owner[0], "name": owner[1]}] if owner else [],
        },
        "spec": {"nodeName": node_name,
                 "containers": [{"name": "c", "resources": {"requests": requests}}],
                 **spec},
        "status": {"phase": phase},
    }


def simulate(monkeypatch, capsys, cluster, *args):
    """Run the script's entry point against `cluster`; return (rc, stdout lines)."""
    data = {"nodes": [], "pods": [], "pvc": [], "pv": [], **cluster}
    monkeypatch.setattr(sim, "kubectl_json", lambda kind, *_: data[kind])
    monkeypatch.setattr(sim.sys, "argv", ["simulate_node_removal.py", *args])
    rc = sim.run()
    return rc, [line for line in capsys.readouterr().out.splitlines() if line]


def findings(lines, check):
    return [line for line in lines if line.startswith(f"{check}: ")]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("2", 2.0), ("500m", 0.5), ("1Ki", 1024.0), ("1Gi", 2.0 ** 30), ("1G", 1e9),
    ("1Ei", 2.0 ** 60), ("1E", 1e18), ("1e3", 1e3), ("1.5Gi", 1.5 * 2 ** 30), (None, 0.0),
])
def test_parse_quantity(value, expected):
    assert sim.parse_quantity(value) == pytest.approx(expected)


@pytest.mark.parametrize("value", ["garbage", "1Xi", "", "1.2.3"])
def test_a_quantity_that_cannot_be_read_raises(value):
    with pytest.raises(ValueError):
        sim.parse_quantity(value)


def test_a_node_at_its_pod_limit_takes_no_more(monkeypatch, capsys):
    full = node("w2", extra={"pods": "1"})
    cluster = {"nodes": [node("w1"), full], "pods": [pod("a", "w1"), pod("b", "w2")]}
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1
    assert "short 1 more pods" in findings(out, "capacity")[0]


def test_match_fields_pin_a_pod_to_its_node(monkeypatch, capsys):
    pinned = {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
        {"matchFields": [{"key": "metadata.name", "operator": "In", "values": ["w1"]}]}]}}}
    cluster = {"nodes": [node("w1"), node("w2")], "pods": [pod("p", "w1", affinity=pinned)]}
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1
    assert findings(out, "last-capable-node")


@pytest.mark.parametrize("op,values,labels,expected", [
    ("In", ["a"], {"k": "a"}, True),
    ("In", ["a"], {"k": "b"}, False),
    ("NotIn", ["a"], {"k": "b"}, True),
    ("NotIn", ["a"], {"k": "a"}, False),
    ("Exists", [], {"k": "x"}, True),
    ("Exists", [], {}, False),
    ("DoesNotExist", [], {}, True),
    ("DoesNotExist", [], {"k": "x"}, False),
    ("Gt", ["3"], {"k": "4"}, True),
    ("Gt", ["3"], {"k": "3"}, False),
    ("Lt", ["3"], {"k": "2"}, True),
    ("Lt", ["3"], {"k": "nan-ish"}, False),
    ("Bogus", [], {"k": "x"}, False),
])
def test_selector_match_expressions(op, values, labels, expected):
    sel = {"matchExpressions": [{"key": "k", "operator": op, "values": values}]}
    assert sim.selector_matches(labels, sel) is expected


def test_selector_match_labels():
    assert sim.selector_matches({"app": "x", "tier": "y"}, {"matchLabels": {"app": "x"}})
    assert not sim.selector_matches({"app": "z"}, {"matchLabels": {"app": "x"}})


@pytest.mark.parametrize("toleration,expected", [
    ({"operator": "Exists"}, True),
    ({"key": "gpu", "operator": "Exists"}, True),
    ({"key": "gpu", "operator": "Exists", "effect": "NoExecute"}, False),
    ({"key": "gpu", "value": "true"}, True),
    ({"key": "gpu", "value": "false"}, False),
    ({"key": "other", "operator": "Exists"}, False),
])
def test_toleration_matches(toleration, expected):
    taint = {"key": "gpu", "value": "true", "effect": "NoSchedule"}
    assert sim.toleration_matches(toleration, taint) is expected


def test_prefer_no_schedule_taints_are_ignored():
    assert sim.node_tolerates_taints([{"key": "k", "effect": "PreferNoSchedule"}], [])


# ---------------------------------------------------------------------------
# End-to-end checks through run()
# ---------------------------------------------------------------------------

def test_no_findings_when_remaining_node_has_room(monkeypatch, capsys):
    cluster = {"nodes": [node("w1"), node("w2")], "pods": [pod("p", "w1", cpu="2")]}
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert (rc, out) == (0, [])


def test_capacity_shortfall_is_refused(monkeypatch, capsys):
    cluster = {
        "nodes": [node("w1"), node("w2", cpu="4")],
        "pods": [pod("evicted", "w1", cpu="3"), pod("resident", "w2", cpu="2", owner=("ReplicaSet", "other"))],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1
    [line] = findings(out, "capacity")
    assert "default/app-rs" in line and "closest is w2" in line and "1 more cpu" in line


def test_capacity_is_per_pod_not_aggregate(monkeypatch, capsys):
    # w2 alone fits each pod, but not both: the second placement must see
    # the first one's usage.
    cluster = {
        "nodes": [node("w1"), node("w2", cpu="4")],
        "pods": [pod("a", "w1", cpu="3", owner=("ReplicaSet", "a")),
                 pod("b", "w1", cpu="3", owner=("ReplicaSet", "b"))],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1
    assert len(findings(out, "capacity")) == 1


def test_room_on_an_ineligible_node_does_not_count(monkeypatch, capsys):
    # The pod is pinned to pool=a; the only other pool=a node is full, and
    # the big pool=b node it may not use must not make it pass.
    cluster = {
        "nodes": [node("w1", labels={"pool": "a"}), node("w2", cpu="1", labels={"pool": "a"}),
                  node("big", cpu="64", labels={"pool": "b"})],
        "pods": [pod("p", "w1", cpu="2", nodeSelector={"pool": "a"})],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1
    assert "closest is w2" in findings(out, "capacity")[0]


def test_hardest_pod_is_placed_first(monkeypatch, capsys):
    # Order matters: the pinned pod must take w2 before the flexible one does,
    # or the flexible pod (which also fits w3) would crowd it out.
    cluster = {
        "nodes": [node("w1"), node("w2", cpu="4", labels={"pool": "a"}), node("w3", cpu="4")],
        "pods": [pod("flex", "w1", cpu="3", owner=("ReplicaSet", "flex")),
                 pod("pinned", "w1", cpu="3", owner=("ReplicaSet", "pinned"), nodeSelector={"pool": "a"})],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "--plan", "w1")
    assert rc == 0, out
    assert "  default/pinned -> w2" in out and "  default/flex -> w3" in out


def test_last_gpu_node_is_refused(monkeypatch, capsys):
    cluster = {
        "nodes": [node("gpu1", extra={"nvidia.com/gpu": "1"}), node("w2")],
        "pods": [pod("model", "gpu1", extra_requests={"nvidia.com/gpu": "1"})],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "gpu1")
    assert rc == 1
    [line] = findings(out, "last-capable-node")
    assert "nvidia.com/gpu" in line
    assert not findings(out, "capacity")  # not double-reported


def test_untolerated_taint_is_refused(monkeypatch, capsys):
    taint = [{"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}]
    cluster = {"nodes": [node("w1"), node("cp1", taints=taint)], "pods": [pod("p", "w1")]}
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1
    assert "nodeSelector/affinity/taint" in findings(out, "last-capable-node")[0]


def test_tolerated_taint_is_a_valid_target(monkeypatch, capsys):
    taint = [{"key": "node-role.kubernetes.io/control-plane", "effect": "NoSchedule"}]
    cluster = {
        "nodes": [node("w1"), node("cp1", taints=taint)],
        "pods": [pod("p", "w1", tolerations=[{"key": "node-role.kubernetes.io/control-plane",
                                              "operator": "Exists"}])],
    }
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])


def test_required_node_affinity(monkeypatch, capsys):
    affinity = {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {"nodeSelectorTerms": [
        {"matchExpressions": [{"key": "zone", "operator": "In", "values": ["z1"]}]}]}}}
    cluster = {
        "nodes": [node("w1", labels={"zone": "z1"}), node("w2", labels={"zone": "z2"})],
        "pods": [pod("p", "w1", affinity=affinity)],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1 and findings(out, "last-capable-node")


def test_daemonset_static_and_finished_pods_are_ignored(monkeypatch, capsys):
    cluster = {
        "nodes": [node("w1"), node("w2", cpu="1")],
        "pods": [pod("ds", "w1", cpu="4", owner=("DaemonSet", "calico-node")),
                 pod("static", "w1", cpu="4", owner=("Node", "w1")),
                 pod("job", "w1", cpu="4", owner=("Job", "j"), phase="Succeeded")],
    }
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])


def test_terminating_pods_are_ignored(monkeypatch, capsys):
    # --node-lost: the lost node's pod is Terminating and its replacement
    # already runs on w2, which has no room for a second copy.
    stale = pod("old", "w1", cpu="3")
    stale["metadata"]["deletionTimestamp"] = "2026-11-11T00:00:00Z"
    cluster = {
        "nodes": [node("w1"), node("w2", cpu="4")],
        "pods": [stale, pod("new", "w2", cpu="3")],
    }
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])


def test_required_pod_anti_affinity_is_refused(monkeypatch, capsys):
    anti = {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"topologyKey": "kubernetes.io/hostname", "labelSelector": {"matchLabels": {"app": "db"}}}]}}
    cluster = {
        "nodes": [node("w1"), node("w2")],
        "pods": [pod("db-0", "w1", labels={"app": "db"}, affinity=anti),
                 pod("db-1", "w2", labels={"app": "db"}, affinity=anti)],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1
    assert "requires 2 distinct kubernetes.io/hostname" in findings(out, "spread")[0]


def test_topology_spread_do_not_schedule(monkeypatch, capsys):
    tsc = [{"maxSkew": 1, "topologyKey": "zone", "whenUnsatisfiable": "DoNotSchedule",
            "labelSelector": {"matchLabels": {"app": "web"}}}]
    cluster = {
        "nodes": [node("w1", labels={"zone": "a"}), node("w2", labels={"zone": "a"}),
                  node("w3", labels={"zone": "b"}), node("w4")],
        "pods": [pod("web-0", "w1", labels={"app": "web"}, topologySpreadConstraints=tsc),
                 pod("web-1", "w2", labels={"app": "web"}, topologySpreadConstraints=tsc),
                 pod("web-2", "w2", labels={"app": "web"}, topologySpreadConstraints=tsc),
                 pod("web-3", "w2", labels={"app": "web"}, topologySpreadConstraints=tsc)],
    }
    # w2 (zone a) holds 3 pods and w3 (zone b) none: the scheduler puts the
    # evicted pod in b (skew 0 + 1 - 0 = 1), whatever the imbalance already is.
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])
    # Left with w4 only, which has no zone label: no domain to place it in.
    rc, out = simulate(monkeypatch, capsys, cluster, "w1", "w2", "w3")
    assert rc == 1
    assert "has no eligible remaining domain" in findings(out, "spread")[0]


def test_topology_spread_schedule_anyway_is_ignored(monkeypatch, capsys):
    tsc = [{"maxSkew": 1, "topologyKey": "missing", "whenUnsatisfiable": "ScheduleAnyway"}]
    cluster = {"nodes": [node("w1"), node("w2")], "pods": [pod("p", "w1", topologySpreadConstraints=tsc)]}
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])


def _pvc(name, volume):
    return {"metadata": {"namespace": "default", "name": name}, "spec": {"volumeName": volume}}


@pytest.mark.parametrize("pv,refused", [
    ({"metadata": {"name": "pv1", "annotations": {"pv.kubernetes.io/provisioned-by": "rancher.io/local-path"}},
      "spec": {"hostPath": {"path": "/opt/local-path"}}}, True),
    ({"metadata": {"name": "pv1"},
      "spec": {"local": {"path": "/mnt/disk"}, "nodeAffinity": {"required": {"nodeSelectorTerms": [
          {"matchExpressions": [{"key": "kubernetes.io/hostname", "operator": "In", "values": ["w1"]}]}]}}}}, True),
    ({"metadata": {"name": "pv1", "annotations": {"pv.kubernetes.io/provisioned-by": "nfs.csi.k8s.io"}},
      "spec": {"csi": {"driver": "nfs.csi.k8s.io"}}}, False),
])
def test_local_data(monkeypatch, capsys, pv, refused):
    cluster = {
        "nodes": [node("w1"), node("w2")],
        "pods": [pod("p", "w1", volumes=[{"name": "data", "persistentVolumeClaim": {"claimName": "data"}}])],
        "pvc": [_pvc("data", "pv1")],
        "pv": [pv],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert bool(findings(out, "local-data")) is refused
    assert rc == (1 if refused else 0)


def test_plan_output(monkeypatch, capsys):
    cluster = {
        "nodes": [node("w1"), node("w2")],
        "pods": [pod("a", "w1", ns="llm", owner=("ReplicaSet", "model")),
                 pod("b", "w1", ns="web", owner=("ReplicaSet", "ui"))],
    }
    rc, out = simulate(monkeypatch, capsys, cluster, "--plan", "w1")
    assert rc == 0
    assert out == ["plan: w1 runs 2 pod(s) (llm 1, web 1)", "  llm/model -> w2", "  web/ui -> w2"]


# ---------------------------------------------------------------------------
# Failing closed: anything that stops the simulation must exit 2, never 0
# (nothing found) or 1 (findings) — the installer refuses the removal on it.
# ---------------------------------------------------------------------------

def test_no_target_nodes_exits_2(monkeypatch, capsys):
    rc, _ = simulate(monkeypatch, capsys, {})
    assert rc == 2


def test_kubectl_failure_exits_2(monkeypatch, capsys):
    def fail(*args):
        raise subprocess.CalledProcessError(1, ["kubectl", "get", *args], stderr="connection refused")

    monkeypatch.setattr(sim, "kubectl_json", fail)
    monkeypatch.setattr(sim.sys, "argv", ["simulate_node_removal.py", "w1"])
    assert sim.run() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "kubectl failed: connection refused" in captured.err


def test_missing_kubectl_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(sim.shutil, "which", lambda _: None)
    monkeypatch.setattr(sim.sys, "argv", ["simulate_node_removal.py", "w1"])
    assert sim.run() == 2
    assert "kubectl not found" in capsys.readouterr().err


def test_malformed_object_exits_2(monkeypatch, capsys):
    broken = {"spec": {"nodeName": "w1"}, "status": {"phase": "Running"}}  # no metadata
    rc, out = simulate(monkeypatch, capsys, {"nodes": [node("w1"), node("w2")], "pods": [broken]}, "w1")
    assert (rc, out) == (2, [])


def test_a_pod_without_a_controller_is_not_placed(monkeypatch, capsys):
    # The drain deletes it; nothing brings it back.
    cluster = {"nodes": [node("w1"), node("w2", cpu="1")], "pods": [pod("bare", "w1", cpu="6", owner=None)]}
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])


def test_a_terminating_statefulset_pod_is_placed(monkeypatch, capsys):
    # Its controller starts the replacement only once the old pod is gone.
    old = pod("db-0", "w1", cpu="6", owner=("StatefulSet", "db"))
    old["metadata"]["deletionTimestamp"] = "2026-11-12T00:00:00Z"
    cluster = {"nodes": [node("w1"), node("w2", cpu="4")], "pods": [old]}
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1
    assert findings(out, "capacity")


def test_an_empty_toleration_value_matches_a_taint_without_one(monkeypatch, capsys):
    taint = {"key": "dedicated", "effect": "NoSchedule"}
    tol = [{"key": "dedicated", "operator": "Equal", "value": "", "effect": "NoSchedule"}]
    cluster = {"nodes": [node("w1"), node("w2", taints=[taint])], "pods": [pod("p", "w1", tolerations=tol)]}
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])


def test_init_container_requests_are_not_added_to_the_containers():
    p = pod("x", "w1", cpu="1")
    p["spec"]["initContainers"] = [{"name": "init", "resources": {"requests": {"cpu": "2"}}},
                                   {"name": "sidecar", "restartPolicy": "Always", "resources": {"requests": {"cpu": "500m"}}}]
    assert sim.pod_requests(p)["cpu"] == 2.0
    p["spec"]["initContainers"][0]["resources"]["requests"]["cpu"] = "1"
    assert sim.pod_requests(p)["cpu"] == 1.5
    # A sidecar started before an init container runs alongside it.
    p["spec"]["initContainers"].reverse()
    p["spec"]["initContainers"][1]["resources"]["requests"]["cpu"] = "2"
    assert sim.pod_requests(p)["cpu"] == 2.5


def test_runtime_class_overhead_is_added_to_the_requests():
    p = pod("x", "w1", cpu="1", overhead={"cpu": "250m", "memory": "160Mi"})
    assert sim.pod_requests(p)["cpu"] == 1.25
    assert sim.pod_requests(p)["memory"] == 2 ** 30 + 160 * 2 ** 20


def test_overhead_of_the_moved_pod_counts(monkeypatch, capsys):
    cluster = {"nodes": [node("w1"), node("w2", cpu="4")], "pods": [pod("p", "w1", cpu="3")]}
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])
    cluster["pods"][0]["spec"]["overhead"] = {"cpu": "1500m"}
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1 and "0.5 more cpu" in findings(out, "capacity")[0]


def test_overhead_of_a_pod_already_on_the_target_counts(monkeypatch, capsys):
    cluster = {"nodes": [node("w1"), node("w2", cpu="4")],
               "pods": [pod("p", "w1", cpu="2"), pod("r", "w2", cpu="1", owner=("ReplicaSet", "r"))]}
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])
    cluster["pods"][1]["spec"]["overhead"] = {"cpu": "1500m"}
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1 and "0.5 more cpu" in findings(out, "capacity")[0]


def _avoid(labels):
    return {"affinity": {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [
        {"topologyKey": "kubernetes.io/hostname", "labelSelector": {"matchLabels": labels}}]}}}


def test_anti_affinity_against_another_app_needs_one_free_node(monkeypatch, capsys):
    b = [pod(f"b-{i}", "w2", labels={"app": "b"}, owner=("ReplicaSet", "b-rs")) for i in range(3)]
    a = pod("a-1", "w1", labels={"app": "a"}, **_avoid({"app": "b"}))
    cluster = {"nodes": [node("w1"), node("w2"), node("w3")], "pods": [a, *b]}
    rc, _ = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 0
    cluster["pods"].append(pod("b-3", "w3", labels={"app": "b"}, owner=("ReplicaSet", "b-rs")))
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1 and "every eligible kubernetes.io/hostname runs a pod it must avoid" in findings(out, "spread")[0]


def test_self_anti_affinity_ignores_pods_the_drain_deletes(monkeypatch, capsys):
    a = {"labels": {"app": "a"}, **_avoid({"app": "a"})}
    cluster = {"nodes": [node("w1"), node("w2")],
               "pods": [pod("a-1", "w1", **a), pod("bare", "w1", owner=None, **a)]}
    assert simulate(monkeypatch, capsys, cluster, "w1") == (0, [])
    # A pod without a controller on a remaining node still takes its node.
    cluster["pods"][1] = pod("bare", "w2", owner=None, **a)
    rc, out = simulate(monkeypatch, capsys, cluster, "w1")
    assert rc == 1 and "requires 2 distinct kubernetes.io/hostname" in findings(out, "spread")[0]
