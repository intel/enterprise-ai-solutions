#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s


@allure.testcase("IEASG-T682")
def test_seaweedfs_s3_ready():
    deploys = list(kr8s.get("deployments", namespace="monitoring",
                            field_selector={"metadata.name": "seaweedfs-telemetry-s3"}))
    assert len(deploys) > 0
    assert (deploys[0].status.readyReplicas or 0) >= 1
