#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg, is_openshift

pytestmark = pytest.mark.skipif(
    cfg.get("object_store_backend", "seaweedfs") != "seaweedfs"
    or (is_openshift() and not cfg.get("observability_enabled", True)),
    reason="the platform's SeaweedFS is not installed (object_store_backend is not seaweedfs, or "
           "OpenShift without observability, where it has no store to back)"
)


@allure.testcase("IEASG-T682")
def test_seaweedfs_s3_ready():
    deploys = list(kr8s.get("deployments", namespace="monitoring",
                            field_selector={"metadata.name": "telemetry-seaweedfs-s3"}))
    assert len(deploys) > 0
    assert (deploys[0].status.readyReplicas or 0) >= 1
