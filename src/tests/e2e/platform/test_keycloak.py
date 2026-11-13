#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s

from conftest import is_openshift

# OpenShift takes the operator from OperatorHub, where it ships as the Red Hat
# build of Keycloak under its own name.
KEYCLOAK_OPERATOR = "rhbk-operator" if is_openshift() else "keycloak-operator"


@allure.testcase("IEASG-T680")
def test_keycloak_operator_ready():
    deploys = list(kr8s.get("deployments", namespace="keycloak",
                            field_selector={"metadata.name": KEYCLOAK_OPERATOR}))
    assert len(deploys) > 0
    assert (deploys[0].status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T681")
def test_keycloak_statefulset_ready():
    sts = list(kr8s.get("statefulsets", namespace="keycloak",
                        field_selector={"metadata.name": "keycloak"}))[0]
    assert (sts.status.readyReplicas or 0) >= 1
