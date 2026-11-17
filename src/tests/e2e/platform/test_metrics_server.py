#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg, is_openshift

pytestmark = pytest.mark.skipif(
    not cfg.get("metrics_server_enabled", True) or is_openshift(),
    reason="the platform does not install metrics-server (metrics_server_enabled is false, or "
           "OpenShift, which serves metrics.k8s.io from its own monitoring)"
)

NAMESPACE = "metrics-server"
REPLICAS = int(cfg.get("metrics_server_replicas", 1))
METRICS_API = "v1beta1.metrics.k8s.io"


def metrics_apiservice():
    apiservices = list(kr8s.get("apiservices", field_selector={"metadata.name": METRICS_API}))
    assert apiservices, f"{METRICS_API} is not registered"
    return apiservices[0]


@allure.testcase("DSPTV-T425")
def test_metrics_server_deployment_ready():
    """The deployment is only ours to check when it is the one behind metrics.k8s.io.

    roles/metrics_server leaves an existing install alone and validates the
    deployment only when the APIService points at its own namespace.
    """
    backend = metrics_apiservice().spec.get("service") or {}
    if backend.get("namespace") != NAMESPACE:
        pytest.skip(f"{METRICS_API} is served from "
                    f"{backend.get('namespace')}/{backend.get('name')}, not by this platform")
    deploys = list(kr8s.get("deployments", namespace=NAMESPACE,
                            field_selector={"metadata.name": "metrics-server"}))
    assert deploys, f"metrics-server not found in {NAMESPACE}"
    assert (deploys[0].status.readyReplicas or 0) >= REPLICAS


@allure.testcase("DSPTV-T426")
def test_metrics_api_available():
    conditions = metrics_apiservice().status.conditions or []
    assert any(c.type == "Available" and c.status == "True" for c in conditions), \
        f"{METRICS_API} is not Available, so kubectl top and the CPU/memory tiles stay empty"


@allure.testcase("DSPTV-T427")
def test_node_metrics_readable():
    assert list(kr8s.get("nodes.metrics.k8s.io")), "the metrics API returns no NodeMetrics"
