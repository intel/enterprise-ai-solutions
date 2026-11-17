#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg
from k8s_helper import assert_daemonset_ready, assert_deployment_ready, get_one, is_openshift

pytestmark = pytest.mark.skipif(
    not cfg.get("nfd_enabled", True),
    reason="nfd_enabled is false for this deployment"
)

NAMESPACE = cfg.get("nfd_namespace", "node-feature-discovery")
MASTER = "node-feature-discovery-master"
CPU_VENDOR_LABEL = "feature.node.kubernetes.io/cpu-model.vendor_id"


def worker():
    """Namespace and name of the worker DaemonSet, which the operator renames."""
    if is_openshift():
        return "openshift-nfd", "nfd-worker"
    return NAMESPACE, "node-feature-discovery-worker"


def worker_node_names():
    namespace, name = worker()
    selector = get_one("daemonsets", name, namespace).spec["selector"]["matchLabels"]
    pods = kr8s.get("pods", namespace=namespace, label_selector=selector)
    return {pod.spec["nodeName"] for pod in pods if pod.spec.get("nodeName")}


@allure.testcase("DSPTV-T428")
def test_nfd_deployed():
    if is_openshift():
        instances = list(kr8s.get("nodefeaturediscoveries.nfd.openshift.io", namespace=kr8s.ALL))
        assert instances, "no NodeFeatureDiscovery instance found, so the operator has nothing to run"
        return
    assert_daemonset_ready(*worker())
    assert_deployment_ready(NAMESPACE, MASTER)


@allure.testcase("DSPTV-T429")
def test_nodes_carry_cpu_feature_labels():
    running_on = worker_node_names()
    assert running_on, f"DaemonSet {worker()[1]} has no pod on any node, so nothing gets labelled"
    labelled = {node.name for node in kr8s.get("nodes", label_selector=CPU_VENDOR_LABEL)}
    missing = sorted(running_on - labelled)
    assert not missing, \
        f"NFD runs on {sorted(running_on)} but {missing} lack {CPU_VENDOR_LABEL}; " \
        "model scheduling matches on those labels"
