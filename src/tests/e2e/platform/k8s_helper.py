#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

"""Cluster queries shared by the platform checks."""

import functools

import kr8s
import pytest

OPENSHIFT_API_GROUPS = ("security.openshift.io", "project.openshift.io")


@functools.cache
def is_openshift():
    """True when the cluster under test is OpenShift.

    Read from discovery only, which every authenticated user may do — so a
    kubeconfig without cluster-scoped read access works too. global_config.yaml
    cannot answer this: preflight resolves the platform at run time and never
    writes cluster_distribution there.
    """
    groups = {api_version.split("/")[0] for api_version in kr8s.api().api_versions()}
    return not groups.isdisjoint(OPENSHIFT_API_GROUPS)


def get_one(kind, name, namespace=None):
    """The object of that kind and name, or a failure naming what is missing.

    Resolving the kind through a string makes kr8s raise ValueError when the
    cluster does not serve it at all, which on OpenShift means the operator
    behind it was never installed. Worth saying so rather than letting an
    IndexError stand in for it.
    """
    where = f" in {namespace}" if namespace else ""
    try:
        objs = list(kr8s.get(kind, namespace=namespace,
                             field_selector={"metadata.name": name}))
    except ValueError as unknown_kind:
        pytest.fail(f"{kind} is not served by the cluster, so its operator is missing: {unknown_kind}")
    assert objs, f"{kind}/{name} not found{where}"
    return objs[0]


def has_condition(obj, condition):
    return any(c.type == condition and c.status == "True"
               for c in (obj.status.conditions or []))


def assert_condition(kind, name, condition, namespace=None, hint=""):
    obj = get_one(kind, name, namespace)
    assert has_condition(obj, condition), \
        f"{kind}/{name} is not {condition}{hint}: {obj.status.conditions}"


def assert_daemonset_ready(namespace, name):
    daemonset = get_one("daemonsets", name, namespace)
    ready = daemonset.status.numberReady
    assert ready > 0, f"daemonsets/{name} in {namespace} has no ready pod"
    assert ready == daemonset.status.desiredNumberScheduled, \
        f"daemonsets/{name} in {namespace} is ready on {ready} of " \
        f"{daemonset.status.desiredNumberScheduled} nodes it targets"


def assert_deployment_ready(namespace, name, replicas=1):
    deployment = get_one("deployments", name, namespace)
    ready = deployment.status.readyReplicas or 0
    assert ready >= replicas, \
        f"deployments/{name} in {namespace} has {ready} ready replica(s), expected {replicas}"


def assert_statefulset_ready(namespace, name, replicas=1):
    statefulset = get_one("statefulsets", name, namespace)
    ready = statefulset.status.readyReplicas or 0
    assert ready >= replicas, \
        f"statefulsets/{name} in {namespace} has {ready} ready replica(s), expected {replicas}"


def skip_unless_installed(condition, reason):
    """A module-wide skip whose condition is a string, evaluated at setup.

    A condition that asks the cluster — anything calling is_openshift() — must
    not run at import time: an unreachable cluster would then fail collection,
    and the module's checks would report nothing at all instead of failing with
    their Zephyr case attached. pytest evaluates a string condition at setup,
    against the namespace of the module under test, so whatever it names has to
    be imported there and will look unused to a linter.
    """
    return pytest.mark.skipif(condition, reason=reason)
