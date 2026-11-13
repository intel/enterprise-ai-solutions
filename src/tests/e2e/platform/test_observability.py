#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg, is_openshift

pytestmark = pytest.mark.skipif(
    not cfg.get("observability_enabled", True),
    reason="observability_enabled is false for this deployment"
)

NAMESPACE = "monitoring"
# OpenShift serves metrics from the cluster's own monitoring and gets the rest from
# instances of the OperatorHub operators, so the objects to check are different ones.
# Names and readiness conditions follow observability_openshift_instances and the
# user_workload_monitoring prerequisite in roles/observability.
UWM_NAMESPACE = "openshift-user-workload-monitoring"
CLUSTER_MONITORING_NAMESPACE = "openshift-monitoring"
GRAFANA = "grafana-platform"


def assert_condition(kind, name, condition, namespace=NAMESPACE):
    try:
        objs = list(kr8s.get(kind, namespace=namespace, field_selector={"metadata.name": name}))
    except ValueError as unknown_kind:
        # kr8s raises this for a kind the cluster does not serve, which here means
        # the operator that owns it was never installed.
        pytest.fail(f"{kind} is not served by the cluster, so its operator is missing: {unknown_kind}")
    assert objs, f"{kind}/{name} not found in {namespace}"
    conditions = objs[0].status.conditions or []
    assert any(c.type == condition and c.status == "True" for c in conditions), \
        f"{kind}/{name} is not {condition}: {conditions}"


@allure.testcase("IEASG-T683")
def test_prometheus_operator_ready():
    if is_openshift():
        assert_condition("prometheuses.monitoring.coreos.com", "user-workload", "Available",
                         namespace=UWM_NAMESPACE)
        return
    deploys = list(kr8s.get("deployments", namespace="monitoring",
                            field_selector={"metadata.name": "kube-prometheus-stack-operator"}))
    assert len(deploys) > 0
    assert (deploys[0].status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T684")
def test_grafana_ready():
    if is_openshift():
        assert_condition("deployments", f"{GRAFANA}-deployment", "Available")
        return
    deploys = list(kr8s.get("deployments", namespace="monitoring",
                            field_selector={"metadata.name": "kube-prometheus-stack-grafana"}))
    assert len(deploys) > 0
    assert (deploys[0].status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T685")
def test_node_exporter_daemonset_ready():
    namespace, name = "monitoring", "kube-prometheus-stack-prometheus-node-exporter"
    if is_openshift():
        namespace, name = CLUSTER_MONITORING_NAMESPACE, "node-exporter"
    ds = list(kr8s.get("daemonsets", namespace=namespace,
              field_selector={"metadata.name": name}))[0]
    assert ds.status.numberReady == ds.status.desiredNumberScheduled


@allure.testcase("IEASG-T686")
def test_loki_ready():
    if is_openshift():
        assert_condition("lokistacks.loki.grafana.com", "logging-loki", "Ready")
        assert_condition("clusterlogforwarders.observability.openshift.io", "platform-logs", "Ready")
        return
    sts = list(kr8s.get("statefulsets", namespace="monitoring",
                        field_selector={"metadata.name": "loki"}))[0]
    assert (sts.status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T687")
def test_tempo_ready():
    if is_openshift():
        assert_condition("tempomonolithics.tempo.grafana.com", "tempo", "Ready")
        return
    sts = list(kr8s.get("statefulsets", namespace="monitoring",
                        field_selector={"metadata.name": "tempo"}))[0]
    assert (sts.status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T688")
def test_otel_collector_daemonset_ready():
    if is_openshift():
        assert_condition("deployments", "opentelemetry-collector", "Available")
        return
    ds = list(kr8s.get("daemonsets", namespace="monitoring",
              field_selector={"metadata.name": "opentelemetry-collector-agent"}))[0]
    assert ds.status.numberReady == ds.status.desiredNumberScheduled
