#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg
from k8s_helper import assert_condition, assert_deployment_ready, get_one, is_openshift, skip_unless_installed

pytestmark = skip_unless_installed(
    "not cfg.get('metrics_server_enabled', True) or is_openshift()",
    reason="the platform does not install metrics-server (metrics_server_enabled is false, or "
           "OpenShift, which serves metrics.k8s.io from its own monitoring)"
)

NAMESPACE = cfg.get("metrics_server_namespace", "metrics-server")
REPLICAS = int(cfg.get("metrics_server_replicas", 1))
METRICS_API = "v1beta1.metrics.k8s.io"


@allure.testcase("DSPTV-T425")
def test_metrics_server_deployment_ready():
    """The deployment is only ours to check when it is the one behind metrics.k8s.io.

    roles/metrics_server leaves an existing install alone and validates the
    deployment only when the APIService points at its own namespace.
    """
    backend = get_one("apiservices", METRICS_API).spec.get("service") or {}
    if backend.get("namespace") != NAMESPACE:
        pytest.skip(f"{METRICS_API} is served from "
                    f"{backend.get('namespace')}/{backend.get('name')}, not by this platform")
    assert_deployment_ready(NAMESPACE, "metrics-server", REPLICAS)


@allure.testcase("DSPTV-T426")
def test_metrics_api_available():
    assert_condition("apiservices", METRICS_API, "Available",
                     hint=", so kubectl top and the CPU/memory tiles stay empty")


@allure.testcase("DSPTV-T427")
def test_node_metrics_readable():
    assert list(kr8s.get("nodes.metrics.k8s.io")), "the metrics API returns no NodeMetrics"
