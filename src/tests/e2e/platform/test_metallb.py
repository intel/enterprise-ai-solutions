#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s

from conftest import cfg
from k8s_helper import assert_deployment_ready, is_openshift, skip_unless_installed

pytestmark = skip_unless_installed(
    "not cfg.get('metallb_enabled', True) or is_openshift()",
    reason="MetalLB not installed by the platform (metallb_enabled is false, or OpenShift, "
           "where the router provides it)"
)


@allure.testcase("DSPTV-T402")
def test_metallb_controller_ready():
    assert_deployment_ready("metallb-system", "metallb-controller")


@allure.testcase("DSPTV-T403")
def test_metallb_ip_address_pool_exists():
    pools = list(kr8s.get("ipaddresspools.metallb.io", namespace="metallb-system"))
    assert len(pools) > 0, "No IPAddressPool found"
