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
WORKER_NAMESPACE, WORKER = ("openshift-nfd", "nfd-worker") if is_openshift() \
    else (NAMESPACE, "node-feature-discovery-worker")


def nfd_worker():
    daemonsets = list(kr8s.get("daemonsets", namespace=WORKER_NAMESPACE,
                               field_selector={"metadata.name": WORKER}))
    assert daemonsets, f"DaemonSet {WORKER} not found in {WORKER_NAMESPACE}"
    return daemonsets[0]


@allure.testcase("DSPTV-T428")
def test_nfd_deployed():
    if is_openshift():
        instances = list(kr8s.get("nodefeaturediscoveries.nfd.openshift.io", namespace=kr8s.ALL))
        assert instances, "no NodeFeatureDiscovery instance found, so the operator has nothing to run"
        return
    worker = nfd_worker()
    assert worker.status.numberReady == worker.status.desiredNumberScheduled


@allure.testcase("DSPTV-T429")
def test_nodes_carry_cpu_feature_labels():
    targeted = nfd_worker().status.desiredNumberScheduled
    assert targeted > 0, f"DaemonSet {WORKER} is scheduled on no node, so nothing gets labelled"
    labelled = list(kr8s.get("nodes", label_selector=CPU_VENDOR_LABEL))
    assert len(labelled) == targeted, \
        f"{len(labelled)} of the {targeted} nodes NFD runs on carry {CPU_VENDOR_LABEL}; " \
        "model scheduling matches on those labels"
