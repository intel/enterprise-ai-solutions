#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

pytestmark = pytest.mark.not_on_openshift(
    reason="Observability is not installed on OpenShift: the cluster monitoring provides it"
)


@allure.testcase("IEASG-T683")
def test_prometheus_operator_ready():
    deploys = list(kr8s.get("deployments", namespace="monitoring",
                            field_selector={"metadata.name": "kube-prometheus-stack-operator"}))
    assert len(deploys) > 0
    assert (deploys[0].status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T684")
def test_grafana_ready():
    deploys = list(kr8s.get("deployments", namespace="monitoring",
                            field_selector={"metadata.name": "kube-prometheus-stack-grafana"}))
    assert len(deploys) > 0
    assert (deploys[0].status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T685")
def test_node_exporter_daemonset_ready():
    ds = list(kr8s.get("daemonsets", namespace="monitoring",
              field_selector={"metadata.name": "kube-prometheus-stack-prometheus-node-exporter"}))[0]
    assert ds.status.numberReady == ds.status.desiredNumberScheduled


@allure.testcase("IEASG-T686")
def test_loki_ready():
    sts = list(kr8s.get("statefulsets", namespace="monitoring",
                        field_selector={"metadata.name": "loki"}))[0]
    assert (sts.status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T687")
def test_tempo_ready():
    sts = list(kr8s.get("statefulsets", namespace="monitoring",
                        field_selector={"metadata.name": "tempo"}))[0]
    assert (sts.status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T688")
def test_otel_collector_daemonset_ready():
    ds = list(kr8s.get("daemonsets", namespace="monitoring",
              field_selector={"metadata.name": "opentelemetry-collector-agent"}))[0]
    assert ds.status.numberReady == ds.status.desiredNumberScheduled
