#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s

from conftest import is_openshift


@allure.testcase("IEASG-T671")
def test_istiod_available():
    deploys = list(kr8s.get("deployments", namespace="istio-system",
                            field_selector={"metadata.name": "istiod"}))
    assert len(deploys) > 0, "istiod not found"
    conditions = deploys[0].status.conditions
    assert any(c.type == "Available" and c.status == "True" for c in conditions)


@allure.testcase("IEASG-T672")
def test_ztunnel_daemonset_ready():
    ds = list(kr8s.get("daemonsets", namespace="ztunnel" if is_openshift() else "istio-system",
                       field_selector={"metadata.name": "ztunnel"}))[0]
    assert ds.status.numberReady > 0
    assert ds.status.numberReady == ds.status.desiredNumberScheduled
