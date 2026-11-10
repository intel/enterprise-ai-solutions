# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import copy
import importlib.util
import pathlib
import re

import jinja2
import pytest
import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("rack_profile_parser", _ROOT / "filter_plugins" / "rack_profile_parser.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
EXAMPLE = yaml.safe_load((_ROOT / "configs" / "defaults" / "rack-profile.yaml.example").read_text())


def device_map(*devices):
    profile = copy.deepcopy(EXAMPLE)
    profile["storage"]["nodes"] = [{"name": "master1", "devices": list(devices)}]
    result = _mod.rack_profile_parse(profile)
    assert not result["errors"], result["errors"]
    return result["summary"]["ceph_devices"]


def rook_device_names(devices):
    env = jinja2.Environment(trim_blocks=True, undefined=jinja2.StrictUndefined)
    env.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(pattern, replacement, value)
    template = env.from_string((_ROOT / "roles" / "rook_ceph_storage" / "templates" / "ceph-cluster.yaml.j2").read_text())
    values = {
        "rook_ceph_operator_namespace": "rook-ceph", "rook_ceph_image": "ceph", "_rook_storage_node_count": 3,
        "rook_ceph_dashboard_enabled": False, "rook_ceph_devices": {"master1": devices},
        "rook_ceph_public_network": "", "rook_ceph_cluster_network": "",
        "rook_ceph_storage_node_label_key": "role", "rook_ceph_storage_node_label_value": "storage",
        **{f"rook_ceph_{d}_{k}": "1Gi" for d in ("mon", "mgr", "osd") for k in ("memory_limit", "memory_request")},
        "rook_ceph_osd_cpu_request": "1",
    }
    nodes = yaml.safe_load(template.render(**values))["spec"]["storage"]["nodes"]
    return [d["name"] for d in nodes[0]["devices"]]


def test_kernel_names_and_paths_become_dev_paths():
    assert device_map("nvme1n1", "/dev/nvme2n1", "/dev/disk/by-id/nvme-SAMSUNG_A1") == {
        "master1": ["/dev/nvme1n1", "/dev/nvme2n1", "/dev/disk/by-id/nvme-SAMSUNG_A1"]}


def test_rook_gets_kernel_names_bare_and_udev_paths_whole():
    assert rook_device_names(["/dev/nvme1n1", "/dev/disk/by-id/nvme-SAMSUNG_A1"]) == [
        "nvme1n1", "/dev/disk/by-id/nvme-SAMSUNG_A1"]


def storage_result(nodes):
    profile = copy.deepcopy(EXAMPLE)
    profile["storage"]["nodes"] = nodes
    return _mod.rack_profile_validate(profile)


def storage_errors(nodes):
    return storage_result(nodes)["errors"]


@pytest.mark.parametrize("nodes, message", [
    ([{"devices": ["nvme1n1"]}], "storage.nodes[0] needs a 'name'"),
    ([{"name": "master1", "devices": ["nvme1n1"]}, {"name": "master1", "devices": ["nvme1n1"]}],
     "storage.nodes lists 'master1' more than once"),
    ([{"name": "ghost", "devices": ["nvme1n1"]}], "storage.nodes entry 'ghost' is not a node defined under racks"),
    ([{"name": "master1", "devices": []}], "storage node 'master1' needs a non-empty devices list"),
    ([{"name": "master1", "devices": [1]}], "storage node 'master1' needs a non-empty devices list"),
    ([{"name": "master1", "devices": [" nvme1n1 "]}], "storage node 'master1' needs a non-empty devices list"),
    ({"master1": ["nvme1n1"]}, "storage.nodes must be a list, got dict"),
])
def test_storage_node_entry_errors(nodes, message):
    errors = storage_errors(nodes)
    assert any(message in e for e in errors), errors


def test_storage_role_and_storage_nodes_disagree_warns():
    result = storage_result([{"name": "master1", "devices": ["nvme1n1"]}])
    assert result["errors"] == []
    assert any("disagree" in w and "master1, master2" in w for w in result["warnings"])
