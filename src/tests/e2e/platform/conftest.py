#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import functools
import logging
import os

import kr8s
import pytest
import yaml

logger = logging.getLogger(__name__)

cfg = {}


def pytest_addoption(parser):
    parser.addoption("--build-config-dir", action="store",
                     default=os.path.expanduser("~/ai-solutions/env/local/"),
                     help="Path to directory containing global_config.yaml")


def pytest_configure(config):
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)
    config.addinivalue_line("markers", "not_on_openshift(reason): skip on OpenShift")

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


def pytest_runtest_setup(item):
    marker = item.get_closest_marker("not_on_openshift")
    if marker and is_openshift():
        pytest.skip(marker.kwargs["reason"])


@functools.cache
def is_openshift():
    if "cluster_distribution" in cfg:
        return cfg["cluster_distribution"] == "openshift"
    if not cfg.get("existing_kubernetes"):
        return False
    try:
        return bool(list(kr8s.get("clusterversions.config.openshift.io", "version")))
    except ValueError:
        return False
