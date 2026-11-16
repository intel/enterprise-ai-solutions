#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s


@allure.testcase("DSPTV-T404")
def test_envoy_gateway_ready():
    deploys = list(kr8s.get("deployments", namespace="envoy-gateway-system",
                            field_selector={"metadata.name": "envoy-gateway"}))
    assert len(deploys) > 0
    assert (deploys[0].status.readyReplicas or 0) >= 1


@allure.testcase("DSPTV-T405")
def test_gateway_class_exists():
    gcs = list(kr8s.get("gatewayclasses.gateway.networking.k8s.io",
                        field_selector={"metadata.name": "eg"}))
    assert len(gcs) > 0, "GatewayClass 'eg' not found"


@allure.testcase("DSPTV-T406")
def test_gateway_accepted():
    gws = list(kr8s.get("gateways.gateway.networking.k8s.io",
                        namespace="envoy-gateway-system"))
    assert len(gws) > 0
    conditions = gws[0].status.conditions
    assert any(c.type == "Accepted" and c.status == "True" for c in conditions)


@allure.testcase("DSPTV-T407")
def test_gateway_tls_secret_exists():
    secrets = list(kr8s.get("secrets", namespace="envoy-gateway-system",
                            field_selector={"metadata.name": "gateway-tls"}))
    assert len(secrets) > 0
