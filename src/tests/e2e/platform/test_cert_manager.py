#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s

from k8s_helper import assert_deployment_ready

NAMESPACE = "cert-manager"


@allure.testcase("DSPTV-T398")
def test_cert_manager_installed():
    deploys = list(kr8s.get("deployments", namespace=NAMESPACE,
                            label_selector={"app.kubernetes.io/name": "cert-manager"}))
    assert len(deploys) > 0, "cert-manager not installed"


@allure.testcase("DSPTV-T399")
def test_cert_manager_webhook_ready():
    assert_deployment_ready(NAMESPACE, "cert-manager-webhook")
