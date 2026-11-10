#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import time

import allure
import kr8s
import pytest
from kr8s.objects import Deployment, HorizontalPodAutoscaler, Namespace, new_class

from conftest import cfg

pytestmark = pytest.mark.skipif(
    not cfg.get("keda_enabled", False) or cfg.get("cluster_distribution", "kubernetes") == "openshift",
    reason="KEDA not installed by the platform (keda_enabled is false, or OpenShift's Custom Metrics Autoscaler)"
)

APIService = new_class(kind="APIService", version="apiregistration.k8s.io/v1", namespaced=False)
ScaledObject = new_class(kind="ScaledObject", version="keda.sh/v1alpha1", namespaced=True)

NAMESPACE = "keda-e2e"
PROMETHEUS = "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090"


def wait_for(check, timeout=180, interval=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(interval)
    return False


def namespace_exists(name):
    try:
        return Namespace.get(name).exists()
    except kr8s.NotFoundError:
        return False


def scaled_object(name, replicas):
    return ScaledObject({
        "metadata": {"name": name, "namespace": NAMESPACE},
        "spec": {
            "scaleTargetRef": {"name": "scale-me"},
            "minReplicaCount": 1,
            "maxReplicaCount": replicas,
            "triggers": [{
                "type": "prometheus",
                "metadata": {"serverAddress": PROMETHEUS, "query": f"vector({replicas})", "threshold": "1"},
            }],
        },
    })


@pytest.fixture(scope="module")
def scaled_workload():
    if not namespace_exists("keda"):
        pytest.skip("external.metrics.k8s.io is served by another install")
    ns = Namespace({"metadata": {"name": NAMESPACE, "labels": {
        "istio.io/dataplane-mode": "ambient",
        "pod-security.kubernetes.io/enforce": "restricted",
    }}})
    ns.create()
    try:
        Deployment({
            "metadata": {"name": "scale-me", "namespace": NAMESPACE},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": {"app": "scale-me"}},
                "template": {
                    "metadata": {"labels": {"app": "scale-me"}},
                    "spec": {
                        "securityContext": {"runAsNonRoot": True, "runAsUser": 65534,
                                            "seccompProfile": {"type": "RuntimeDefault"}},
                        "containers": [{
                            "name": "pause",
                            "image": "registry.k8s.io/pause:3.10",
                            "resources": {"requests": {"cpu": "10m", "memory": "8Mi"},
                                          "limits": {"cpu": "20m", "memory": "16Mi"}},
                            "securityContext": {"allowPrivilegeEscalation": False,
                                                "capabilities": {"drop": ["ALL"]}},
                        }],
                    },
                },
            },
        }).create()
        so = scaled_object("scale-me", 2)
        so.create()
        yield so
    finally:
        ns.delete()
        wait_for(lambda: not namespace_exists(NAMESPACE), timeout=120)


@allure.testcase("")
def test_keda_serves_external_metrics():
    if namespace_exists("keda"):
        for name in ("keda-operator", "keda-operator-metrics-apiserver", "keda-admission-webhooks"):
            deploy = Deployment.get(name, namespace="keda")
            assert (deploy.status.get("readyReplicas") or 0) >= 1, f"{name} has no ready replica"
    api = APIService.get("v1beta1.external.metrics.k8s.io")
    available = [c for c in api.status.get("conditions", []) if c["type"] == "Available"]
    assert available and available[0]["status"] == "True", f"external.metrics.k8s.io not Available: {available}"


@allure.testcase("")
@pytest.mark.skipif(not cfg.get("observability_enabled", True),
                    reason="no platform Prometheus for the trigger (observability_enabled is false)")
def test_keda_scales_on_prometheus_trigger(scaled_workload):
    deploy = Deployment.get("scale-me", namespace=NAMESPACE)

    def scaled():
        deploy.refresh()
        return (deploy.status.get("readyReplicas") or 0) == 2

    assert wait_for(scaled), f"scale-me did not reach 2 ready replicas: {deploy.status}"
    hpa = HorizontalPodAutoscaler.get("keda-hpa-scale-me", namespace=NAMESPACE)
    assert hpa.spec["metrics"][0]["type"] == "External"


@allure.testcase("")
def test_keda_webhook_rejects_duplicate_scaledobject(scaled_workload):
    with pytest.raises(kr8s.ServerError, match="already managed by the ScaledObject"):
        scaled_object("scale-me-duplicate", 2).create()
