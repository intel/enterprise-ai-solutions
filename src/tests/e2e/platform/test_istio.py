#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure

from k8s_helper import assert_condition, assert_daemonset_ready, is_openshift


def ztunnel_namespace():
    return "ztunnel" if is_openshift() else "istio-system"


@allure.testcase("DSPTV-T400")
def test_istiod_available():
    assert_condition("deployments", "istiod", "Available", namespace="istio-system")


@allure.testcase("DSPTV-T401")
def test_ztunnel_daemonset_ready():
    assert_daemonset_ready(ztunnel_namespace(), "ztunnel")
