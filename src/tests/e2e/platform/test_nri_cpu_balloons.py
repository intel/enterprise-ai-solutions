#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import pytest

from conftest import cfg
from k8s_helper import assert_daemonset_ready, get_one

pytestmark = pytest.mark.skipif(
    cfg.get("kubernetes_cpu_policy", "nri-balloons") != "nri-balloons"
    or cfg.get("kubernetes_accelerator", "cpu") != "cpu",
    reason="NRI balloons not installed by the platform (kubernetes_cpu_policy is not nri-balloons, "
           "or the accelerator is not cpu)"
)

NAMESPACE = "kube-system"
RELEASE = "nri-resource-policy-balloons"


@allure.testcase("DSPTV-T430")
def test_nri_policy_configmap_exists():
    get_one("configmaps", f"{RELEASE}-config", namespace=NAMESPACE)


@allure.testcase("DSPTV-T431")
def test_nri_daemonset_ready():
    assert_daemonset_ready(NAMESPACE, RELEASE)
