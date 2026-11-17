#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure

from k8s_helper import assert_deployment_ready, assert_statefulset_ready, is_openshift


def keycloak_operator():
    return "rhbk-operator" if is_openshift() else "keycloak-operator"


@allure.testcase("DSPTV-T409")
def test_keycloak_operator_ready():
    assert_deployment_ready("keycloak", keycloak_operator())


@allure.testcase("DSPTV-T410")
def test_keycloak_statefulset_ready():
    assert_statefulset_ready("keycloak", "keycloak")
