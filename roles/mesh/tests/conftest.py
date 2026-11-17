# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The modules import mesh_kube as Ansible ships it, ansible.module_utils.mesh_kube."""

import importlib
import os
import sys

_ROLE = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(_ROLE, "module_utils"))
sys.path.insert(0, os.path.join(_ROLE, "library"))
sys.modules["ansible.module_utils.mesh_kube"] = importlib.import_module("mesh_kube")
