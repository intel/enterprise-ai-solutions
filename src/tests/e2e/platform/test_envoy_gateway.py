#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s

from k8s_helper import assert_deployment_ready, assert_exists, has_condition

NAMESPACE = "envoy-gateway-system"


@allure.testcase("DSPTV-T404")
def test_envoy_gateway_ready():
    assert_deployment_ready(NAMESPACE, "envoy-gateway")


@allure.testcase("DSPTV-T405")
def test_gateway_class_exists():
    assert_exists("gatewayclasses.gateway.networking.k8s.io", "eg")


@allure.testcase("DSPTV-T406")
def test_gateway_accepted():
    gateways = list(kr8s.get("gateways.gateway.networking.k8s.io", namespace=NAMESPACE))
    assert gateways, f"no Gateway found in {NAMESPACE}"
    assert has_condition(gateways[0], "Accepted"), \
        f"gateways/{gateways[0].name} is not Accepted: {gateways[0].status.conditions}"


@allure.testcase("DSPTV-T407")
def test_gateway_tls_secret_exists():
    assert_exists("secrets", "gateway-tls", namespace=NAMESPACE)
