#!/usr/bin/env python
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os

import pytest
import yaml

from agentic_support import REPO_ROOT, configure_kubeconfig


def pytest_addoption(parser):
    parser.addoption(
        "--agentic-env",
        action="store",
        default=os.environ.get("ES_ENV", "local"),
        help="Unified installer environment name under env/.",
    )
    parser.addoption(
        "--run-agentic-lifecycle",
        action="store_true",
        help="Allow TC001/TC002 to tear down or install the agentic layer.",
    )
    parser.addoption(
        "--run-agentic-model-operations",
        action="store_true",
        help="Allow model deployment and removal test cases to mutate the cluster.",
    )


@pytest.fixture(scope="session")
def agentic_env_name(request):
    return request.config.getoption("--agentic-env")


@pytest.fixture(scope="session")
def agentic_env(agentic_env_name):
    env_name = agentic_env_name
    env_dir = REPO_ROOT / "env" / env_name
    if not (REPO_ROOT / "es_auto_installer.sh").is_file():
        pytest.fail(f"Unified installer not found under {REPO_ROOT}")
    if not (env_dir / "global_config.yaml").is_file():
        pytest.fail(
            f"Unified environment env/{env_name}/ is not initialized; "
            f"run es_auto_installer.sh init agent-toolkit --env {env_name} first."
        )
    with (env_dir / "global_config.yaml").open(encoding="utf-8") as stream:
        config = yaml.safe_load(stream) or {}
    kubeconfig = configure_kubeconfig(env_name)
    agentic_config_path = env_dir / "config.agent-toolkit.yaml"
    agentic_config = {}
    if agentic_config_path.is_file():
        with agentic_config_path.open(encoding="utf-8") as stream:
            agentic_config = yaml.safe_load(stream) or {}
    return {
        "name": env_name,
        "dir": env_dir,
        "config": config,
        "agentic_config": agentic_config,
        "kubeconfig": kubeconfig,
    }


@pytest.fixture(scope="session")
def installer():
    return [str(REPO_ROOT / "es_auto_installer.sh")]


@pytest.fixture(scope="session")
def model_manager():
    return [str(REPO_ROOT / "model-manager")]


@pytest.fixture(scope="session")
def agentic_target_available():
    package_registry = (
        REPO_ROOT
        / "ext"
        / "enterprise.agentic-ai-stack-core"
        / "components.yaml"
    )
    if not package_registry.is_file():
        pytest.skip(
            "The Toolkit layer is not present under ext/; "
            "run ./es_auto_installer.sh init agent-toolkit --env <name>."
        )
