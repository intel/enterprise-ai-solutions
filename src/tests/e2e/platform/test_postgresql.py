#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s


@allure.testcase("DSPTV-T408")
def test_postgresql_cluster_ready():
    clusters = list(kr8s.get("clusters.postgresql.cnpg.io", namespace="postgresql"))
    assert len(clusters) > 0, "CNPG Cluster not found"
    conditions = clusters[0].status.get("conditions", [])
    assert any(c.get("type") == "Ready" and c.get("status") == "True"
               for c in conditions), f"PostgreSQL not Ready: {conditions}"
