#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import pytest

from conftest import cfg
from k8s_helper import assert_deployment_ready

pytestmark = pytest.mark.skipif(
    cfg.get("object_store_backend", "seaweedfs") != "seaweedfs"
    or not cfg.get("observability_enabled", True),
    reason="the telemetry SeaweedFS is not installed (object_store_backend is not seaweedfs, or "
           "observability is disabled, leaving it with no declared user)"
)


@allure.testcase("DSPTV-T411")
def test_seaweedfs_s3_ready():
    assert_deployment_ready("monitoring", "telemetry-seaweedfs-s3")
