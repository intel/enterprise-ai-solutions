#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import allure
import pytest

from conftest import cfg
from k8s_helper import assert_daemonset_ready, assert_deployment_ready, get_one

pytestmark = pytest.mark.skipif(
    cfg.get("storage_backend") != "nfs",
    reason="NFS storage not configured (storage_backend != nfs)"
)

NAMESPACE = cfg.get("nfs_csi_namespace", "csi-driver-nfs")
STORAGECLASS = cfg.get("nfs_storageclass_name", "nfs-client")
PROVISIONER = "nfs.csi.k8s.io"


@allure.testcase("DSPTV-T418")
def test_nfs_csi_driver_ready():
    """The driver behind the NFS StorageClass runs: controller available, node plugin on every node.

    The node plugin is a DaemonSet, and a PVC binds whether or not it runs: a missing
    plugin shows up only as a mount that fails on that one node, which nothing else in
    the suite would attribute to storage. The controller is covered by every PVC that
    binds, so it is asserted here only to name the culprit when nothing binds.
    """
    assert_deployment_ready(NAMESPACE, "csi-nfs-controller")
    assert_daemonset_ready(NAMESPACE, "csi-nfs-node")


@allure.testcase("DSPTV-T419")
def test_nfs_storageclass_exists():
    """The StorageClass consumers name exists and is provisioned by the CSI driver.

    The provisioner is asserted because a class under this name may be left over from
    the previous driver: install refuses that cluster (a StorageClass cannot be moved
    to another provisioner in place), so a class alone does not mean working storage.
    """
    storageclass = get_one("storageclasses.storage.k8s.io", STORAGECLASS)
    provisioner = storageclass.raw.get("provisioner")
    assert provisioner == PROVISIONER, \
        f"StorageClass {STORAGECLASS} is provisioned by '{provisioner}', expected {PROVISIONER}"
