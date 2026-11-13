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

# Served by OpenShift's aggregated apiservers, so a chart or a CRD cannot put
# them on upstream Kubernetes.
OPENSHIFT_API_GROUPS = ("security.openshift.io", "project.openshift.io")


@functools.cache
def is_openshift():
    """True when the cluster under test is OpenShift.

    Asked once per session, and from discovery only, which every authenticated
    user may do — so a kubeconfig without cluster-scoped read access works too.
    global_config.yaml cannot answer this: preflight resolves the platform at
    run time and never writes cluster_distribution there.
    """
    groups = {api_version.split("/")[0] for api_version in kr8s.api().api_versions()}
    return not groups.isdisjoint(OPENSHIFT_API_GROUPS)


def pytest_addoption(parser):
    parser.addoption("--build-config-dir", action="store",
                     default=os.path.expanduser("~/ai-solutions/env/local/"),
                     help="Path to directory containing global_config.yaml")


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
