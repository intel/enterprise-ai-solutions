# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Setup shared by the filter-plugin unit tests."""
import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
EXAMPLE_PROFILE = yaml.safe_load((ROOT / "configs" / "defaults" / "rack-profile.yaml.example").read_text())


def load_plugin(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "filter_plugins" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
