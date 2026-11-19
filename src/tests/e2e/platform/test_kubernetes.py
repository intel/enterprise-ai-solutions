#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s

@allure.testcase("DSPTV-T395")
def test_cluster_reachable():
    version = kr8s.api().version()
    assert version["major"]


@allure.testcase("DSPTV-T396")
def test_nodes_ready():
    nodes = list(kr8s.get("nodes"))
    assert len(nodes) > 0
    not_ready = [n.name for n in nodes if not any(
        c.type == "Ready" and c.status == "True" for c in n.status.conditions)]
    assert not_ready == [], f"Nodes not Ready: {not_ready}"


@allure.testcase("DSPTV-T397")
def test_kubernetes_min_version():
    version = kr8s.api().version()
    minor = int(version["minor"].rstrip("+"))
    assert minor >= 28, f"K8s v1.{minor} < required v1.28"
