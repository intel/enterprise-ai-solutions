#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg
from k8s_helper import (assert_condition, assert_daemonset_ready, assert_deployment_ready,
                        assert_statefulset_ready, has_condition, is_openshift)

pytestmark = pytest.mark.skipif(
    not cfg.get("observability_enabled", True),
    reason="observability_enabled is false for this deployment"
)

# Names and readiness follow observability_openshift_instances in roles/observability.
NAMESPACE = "monitoring"
UWM_NAMESPACE = "openshift-user-workload-monitoring"
CLUSTER_MONITORING_NAMESPACE = "openshift-monitoring"
GRAFANA = "grafana-platform"
GRAFANA_DATASOURCES = ["loki", "prometheus", "tempo"]


def synced_datasources():
    objs = kr8s.get("grafanadatasources.grafana.integreatly.org", namespace=NAMESPACE)
    return sorted(obj.name for obj in objs if has_condition(obj, "DatasourceSynchronized"))


@allure.testcase("DSPTV-T412")
def test_prometheus_operator_ready():
    if is_openshift():
        assert_condition("prometheuses.monitoring.coreos.com", "user-workload", "Available",
                         namespace=UWM_NAMESPACE)
        return
    assert_deployment_ready(NAMESPACE, "kube-prometheus-stack-operator")


@allure.testcase("DSPTV-T413")
def test_grafana_ready():
    if is_openshift():
        assert_condition("deployments", f"{GRAFANA}-deployment", "Available", namespace=NAMESPACE)
        synced = synced_datasources()
        assert synced == GRAFANA_DATASOURCES, \
            f"datasources synced to Grafana: {synced or 'none'}; expected {GRAFANA_DATASOURCES}"
        return
    assert_deployment_ready(NAMESPACE, "kube-prometheus-stack-grafana")


@allure.testcase("DSPTV-T414")
def test_node_exporter_daemonset_ready():
    if is_openshift():
        assert_daemonset_ready(CLUSTER_MONITORING_NAMESPACE, "node-exporter")
        return
    assert_daemonset_ready(NAMESPACE, "kube-prometheus-stack-prometheus-node-exporter")


@allure.testcase("DSPTV-T415")
def test_loki_ready():
    if is_openshift():
        assert_condition("lokistacks.loki.grafana.com", "logging-loki", "Ready", namespace=NAMESPACE)
        assert_condition("clusterlogforwarders.observability.openshift.io", "platform-logs",
                         "Ready", namespace=NAMESPACE)
        return
    assert_statefulset_ready(NAMESPACE, "loki")


@allure.testcase("DSPTV-T416")
def test_tempo_ready():
    if is_openshift():
        assert_condition("tempomonolithics.tempo.grafana.com", "tempo", "Ready", namespace=NAMESPACE)
        return
    assert_statefulset_ready(NAMESPACE, "tempo")


@allure.testcase("DSPTV-T417")
def test_otel_collector_daemonset_ready():
    if is_openshift():
        assert_condition("deployments", "opentelemetry-collector", "Available", namespace=NAMESPACE)
        return
    assert_daemonset_ready(NAMESPACE, "opentelemetry-collector-agent")
