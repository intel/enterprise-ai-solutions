# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
from plugin_loader import load_plugin

node_pods = load_plugin("node_pods")


def pod(name, owner="DaemonSet", ready=True, conditions=True):
    status = {"conditions": [{"type": "Initialized", "status": "True"},
                             {"type": "Ready", "status": "True" if ready else "False", "reason": "x"}]} if conditions else {}
    meta = {"name": name}
    if owner:
        meta["ownerReferences"] = [{"kind": owner, "name": name.split("-")[0]}]
    return {"metadata": meta, "status": status}


def test_only_daemonset_pods_count():
    pods = [pod("calico-node-a"), pod("web-1", owner="ReplicaSet"), pod("static", owner=None)]
    assert [p["metadata"]["name"] for p in node_pods.daemonset_pods(pods)] == ["calico-node-a"]


def test_unready_pods_are_named():
    pods = [pod("calico-node-a", ready=False), pod("kube-proxy-b"), pod("nodelocaldns-c", conditions=False)]
    assert node_pods.unready_pods(pods) == ["calico-node-a", "nodelocaldns-c"]


def test_no_pods():
    assert node_pods.daemonset_pods(None) == [] and node_pods.unready_pods([]) == []
