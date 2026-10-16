#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Live-cluster smoke tests for the add-node/remove-node CLI actions, in the
# same style as the rest of src/tests/e2e/platform: they assert against a
# real cluster reached via --build-config-dir, not mocks.
#
# Both tests only exercise --dry-run: they run no Kubespray playbook and
# mutate nothing, so they're always safe to run against a live cluster.

import os
import subprocess

import allure


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


@allure.testcase("IEASG-T700")
def test_add_node_dry_run_noop(request):
    result = _run_cli(request, "add-node", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout, result.stdout


@allure.testcase("IEASG-T701")
def test_remove_node_dry_run_noop(request):
    result = _run_cli(request, "remove-node", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout, result.stdout

