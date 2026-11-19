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


_REFUSES_WITH_APP_LAYER = "is not supported on a cluster with"
_NO_TOOLING = "missing. Run './es_auto_installer.sh configure'"


def _assert_dry_run_leaves_the_cluster_alone(result, verb):
    """A dry run either reports nothing to do, or refuses for a reason the installer states.

    On a CI cluster the application layer is installed by the time this suite runs, so the
    refusal is the outcome that gets exercised here rather than the no-op.
    """
    if _NO_TOOLING in result.stderr:
        pytest.skip(f"the installer in this checkout is missing a tool it needs, so {verb} "
                    f"--dry-run cannot run: {result.stderr.strip()}")
    if _REFUSES_WITH_APP_LAYER in result.stderr:
        assert result.returncode != 0, \
            f"{verb} --dry-run reported the application-layer refusal but exited 0:\n{result.stderr}"
        assert "Tear it down first" in result.stderr, \
            f"{verb} --dry-run refused without telling the operator what to do:\n{result.stderr}"
        return
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout, result.stdout


@allure.testcase("DSPTV-T420")
def test_add_node_dry_run_noop(request):
    _assert_dry_run_leaves_the_cluster_alone(_run_cli(request, "add-node", "--dry-run"), "add-node")


@allure.testcase("DSPTV-T421")
def test_remove_node_dry_run_noop(request):
    _assert_dry_run_leaves_the_cluster_alone(_run_cli(request, "remove-node", "--dry-run"), "remove-node")
