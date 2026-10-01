#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import logging
import os

import pytest
import yaml

logger = logging.getLogger(__name__)

cfg = {}


def pytest_addoption(parser):
    parser.addoption("--build-config-dir", action="store",
                     default=os.path.expanduser("~/ai-solutions/env/local/"),
                     help="Path to directory containing global_config.yaml")
    # Opt-in fixture for the add-node/remove-node round trip in test_node_scaling.py:
    # a spare host, not already in nodes.yaml, that the test may join and then remove.
    # Left unset, that test is skipped — only the always-safe --dry-run checks run.
    parser.addoption("--extra-worker-ip", action="store", default=None,
                     help="Spare worker node IP for the add-node/remove-node e2e test")
    parser.addoption("--extra-worker-hostname", action="store", default=None,
                     help="Spare worker node hostname for the add-node/remove-node e2e test")


def pytest_configure(config):
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    build_config_dir = config.getoption("--build-config-dir")
    if not build_config_dir or not os.path.isdir(build_config_dir):
        pytest.fail(f"Build config dir not found: {build_config_dir}")

    filepath = os.path.join(build_config_dir, "global_config.yaml")
    if os.path.exists(filepath):
        logger.debug(f"Loading {filepath}")
        with open(filepath, "r") as f:
            content = yaml.safe_load(f)
        if content:
            cfg.update(content)
