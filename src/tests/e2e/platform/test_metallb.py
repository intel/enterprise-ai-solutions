#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg, is_openshift

pytestmark = pytest.mark.skipif(
    not cfg.get("metallb_enabled", True) or is_openshift(),
    reason="MetalLB not installed by the platform (metallb_enabled is false, or OpenShift, "
           "where the router provides it)"
)


@allure.testcase("IEASG-T673")
def test_metallb_controller_ready():
    deploys = list(kr8s.get("deployments", namespace="metallb-system",
                            field_selector={"metadata.name": "metallb-controller"}))
    assert len(deploys) > 0, "metallb-controller not found"
    assert (deploys[0].status.readyReplicas or 0) >= 1


@allure.testcase("IEASG-T674")
def test_metallb_ip_address_pool_exists():
    pools = list(kr8s.get("ipaddresspools.metallb.io", namespace="metallb-system"))
    assert len(pools) > 0, "No IPAddressPool found"
