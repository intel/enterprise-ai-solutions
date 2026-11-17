#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import kr8s
import pytest

from conftest import cfg

pytestmark = pytest.mark.skipif(
    cfg.get("storage_backend") != "nfs",
    reason="NFS storage not configured (storage_backend != nfs)"
)


@allure.testcase("DSPTV-T418")
def test_nfs_provisioner_running():
    pods = list(kr8s.get("pods", namespace="nfs-provisioner",
                         label_selector={"app": "nfs-subdir-external-provisioner"}))
    assert len(pods) > 0
    assert pods[0].status.phase == "Running"


@allure.testcase("DSPTV-T419")
def test_nfs_storageclass_exists():
    scs = list(kr8s.get("storageclasses.storage.k8s.io"))
    nfs_sc = [sc for sc in scs if "nfs" in sc.name]
    assert len(nfs_sc) > 0, "NFS StorageClass not found"
