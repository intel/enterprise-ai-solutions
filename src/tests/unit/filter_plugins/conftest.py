# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Puts the Ansible filter plugins on sys.path so the tests can import them."""

import sys
from pathlib import Path

PLUGINS = Path(__file__).resolve().parents[4] / "filter_plugins"
sys.path.insert(0, str(PLUGINS))
