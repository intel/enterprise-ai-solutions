#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess

import allure
import pytest

from conftest import cfg

pytestmark = pytest.mark.skipif(
    bool(cfg.get("existing_kubernetes")),
    reason="the installer refuses add-node and remove-node on a BYO cluster "
           "(existing_kubernetes is set), which is how every OpenShift deployment runs"
)


def _installer_root(build_config_dir):
    # --build-config-dir is env/<name>/ inside the installer checkout.
    return os.path.dirname(os.path.dirname(os.path.normpath(build_config_dir)))


def _env_name(build_config_dir):
    return os.path.basename(os.path.normpath(build_config_dir))


def _run_cli(request, *args):
    build_config_dir = request.config.getoption("--build-config-dir")
    root = _installer_root(build_config_dir)
    env = _env_name(build_config_dir)
    cmd = ["./es_auto_installer.sh", *args, "--env", env]
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=300)


@allure.testcase("DSPTV-T420")
def test_add_node_dry_run_noop(request):
    result = _run_cli(request, "add-node", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout, result.stdout


@allure.testcase("DSPTV-T421")
def test_remove_node_dry_run_noop(request):
    result = _run_cli(request, "remove-node", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout, result.stdout
