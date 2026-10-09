#!/usr/bin/env python
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]


def run_cli(command, *args, extra_env=None):
    env = os.environ.copy()
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [*command, *args],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def run_kubectl(agentic_env, *args):
    env = {"KUBECONFIG": str(agentic_env["kubeconfig"])}
    result = run_cli(["kubectl"], *args, extra_env=env)
    assert result.returncode == 0, result.stdout
    return result.stdout.strip()


def configure_kubeconfig(env_name):
    env_dir = REPO_ROOT / "env" / env_name
    global_config_path = env_dir / "global_config.yaml"
    config = {}
    if global_config_path.is_file():
        with global_config_path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream) or {}
    existing_kubeconfig = config.get("existing_kubernetes")
    kubeconfig = (
        Path(existing_kubeconfig).expanduser()
        if existing_kubeconfig
        else env_dir / "kubeconfig.yaml"
    )
    if kubeconfig.is_file():
        os.environ["KUBECONFIG"] = str(kubeconfig)
    return kubeconfig