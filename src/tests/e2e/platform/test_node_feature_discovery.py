#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import kr8s
import pytest

from conftest import cfg, is_openshift

pytestmark = pytest.mark.skipif(
    not cfg.get("nfd_enabled", True),
    reason="nfd_enabled is false for this deployment"
)

NAMESPACE = cfg.get("nfd_namespace", "node-feature-discovery")
CPU_VENDOR_LABEL = "feature.node.kubernetes.io/cpu-model.vendor_id"


def test_nfd_deployed():
    if is_openshift():
        instances = list(kr8s.get("nodefeaturediscoveries.nfd.openshift.io", namespace=kr8s.ALL))
        assert instances, "no NodeFeatureDiscovery instance found, so the operator has nothing to run"
        return
    daemonsets = list(kr8s.get("daemonsets", namespace=NAMESPACE,
                               field_selector={"metadata.name": "node-feature-discovery-worker"}))
    assert daemonsets, f"node-feature-discovery-worker not found in {NAMESPACE}"
    worker = daemonsets[0]
    assert worker.status.numberReady == worker.status.desiredNumberScheduled


def test_nodes_carry_cpu_feature_labels():
    nodes = list(kr8s.get("nodes", label_selector=CPU_VENDOR_LABEL))
    assert nodes, f"no node carries {CPU_VENDOR_LABEL}, so model scheduling has nothing to match on"
