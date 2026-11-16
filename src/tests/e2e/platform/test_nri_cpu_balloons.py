#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import kr8s
import pytest

from conftest import cfg

pytestmark = pytest.mark.skipif(
    cfg.get("kubernetes_cpu_policy", "nri-balloons") != "nri-balloons"
    or cfg.get("kubernetes_accelerator", "cpu") != "cpu",
    reason="NRI balloons not installed by the platform (kubernetes_cpu_policy is not nri-balloons, "
           "or the accelerator is not cpu)"
)

NAMESPACE = "kube-system"
RELEASE = "nri-resource-policy-balloons"


def test_nri_policy_configmap_exists():
    configmaps = list(kr8s.get("configmaps", namespace=NAMESPACE,
                               field_selector={"metadata.name": f"{RELEASE}-config"}))
    assert configmaps, f"policy ConfigMap {RELEASE}-config is missing from {NAMESPACE}"


def test_nri_daemonset_ready():
    daemonsets = list(kr8s.get("daemonsets", namespace=NAMESPACE,
                               field_selector={"metadata.name": RELEASE}))
    assert daemonsets, f"DaemonSet {RELEASE} not found in {NAMESPACE}"
    plugin = daemonsets[0]
    assert plugin.status.numberReady > 0
    assert plugin.status.numberReady == plugin.status.desiredNumberScheduled
