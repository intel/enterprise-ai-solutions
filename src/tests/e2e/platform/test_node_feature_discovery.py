#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg, is_openshift

pytestmark = pytest.mark.skipif(
    not cfg.get("nfd_enabled", True),
    reason="nfd_enabled is false for this deployment"
)

NAMESPACE = cfg.get("nfd_namespace", "node-feature-discovery")
CPU_VENDOR_LABEL = "feature.node.kubernetes.io/cpu-model.vendor_id"
MASTER = "node-feature-discovery-master"
WORKER_NAMESPACE, WORKER = ("openshift-nfd", "nfd-worker") if is_openshift() \
    else (NAMESPACE, "node-feature-discovery-worker")


def nfd_worker():
    daemonsets = list(kr8s.get("daemonsets", namespace=WORKER_NAMESPACE,
                               field_selector={"metadata.name": WORKER}))
    assert daemonsets, f"DaemonSet {WORKER} not found in {WORKER_NAMESPACE}"
    return daemonsets[0]


def worker_node_names():
    """Nodes the worker DaemonSet actually has a pod on."""
    worker = nfd_worker()
    pods = kr8s.get("pods", namespace=WORKER_NAMESPACE,
                    label_selector=worker.spec["selector"]["matchLabels"])
    return {pod.spec["nodeName"] for pod in pods if pod.spec.get("nodeName")}


@allure.testcase("DSPTV-T428")
def test_nfd_deployed():
    if is_openshift():
        instances = list(kr8s.get("nodefeaturediscoveries.nfd.openshift.io", namespace=kr8s.ALL))
        assert instances, "no NodeFeatureDiscovery instance found, so the operator has nothing to run"
        return
    worker = nfd_worker()
    assert worker.status.numberReady > 0, f"DaemonSet {WORKER} has no ready pod"
    assert worker.status.numberReady == worker.status.desiredNumberScheduled
    masters = list(kr8s.get("deployments", namespace=NAMESPACE,
                            field_selector={"metadata.name": MASTER}))
    assert masters, f"Deployment {MASTER} not found in {NAMESPACE}"
    assert (masters[0].status.readyReplicas or 0) >= 1, \
        f"{MASTER} has no ready replica, so nothing turns the workers' features into labels"


@allure.testcase("DSPTV-T429")
def test_nodes_carry_cpu_feature_labels():
    running_on = worker_node_names()
    assert running_on, f"DaemonSet {WORKER} has no pod on any node, so nothing gets labelled"
    labelled = {node.name for node in kr8s.get("nodes", label_selector=CPU_VENDOR_LABEL)}
    missing = sorted(running_on - labelled)
    assert not missing, \
        f"NFD runs on {sorted(running_on)} but {missing} lack {CPU_VENDOR_LABEL}; " \
        "model scheduling matches on those labels"
