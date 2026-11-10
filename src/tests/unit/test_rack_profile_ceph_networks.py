# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import copy
import re

import jinja2
import yaml

from plugin_loader import EXAMPLE_PROFILE as EXAMPLE, ROOT as _ROOT, load_plugin

_mod = load_plugin("rack_profile_parser")


def errors(change):
    profile = copy.deepcopy(EXAMPLE)
    change(profile)
    return _mod.rack_profile_validate(profile)["errors"]


def ceph_network(**variables):
    env = jinja2.Environment(trim_blocks=True, undefined=jinja2.StrictUndefined)
    env.filters["regex_replace"] = lambda value, pattern, replacement="": re.sub(pattern, replacement, value)
    template = env.from_string((_ROOT / "roles" / "rook_ceph_storage" / "templates" / "ceph-cluster.yaml.j2").read_text())
    values = {
        "rook_ceph_operator_namespace": "rook-ceph", "rook_ceph_image": "ceph", "_rook_storage_node_count": 3,
        "rook_ceph_dashboard_enabled": False, "rook_ceph_devices": {"n1": ["/dev/nvme1n1"]},
        "rook_ceph_storage_node_label_key": "role", "rook_ceph_storage_node_label_value": "storage",
        **{f"rook_ceph_{d}_{k}": "1Gi" for d in ("mon", "mgr", "osd") for k in ("memory_limit", "memory_request")},
        "rook_ceph_osd_cpu_request": "1", **variables,
    }
    return yaml.safe_load(template.render(**values))["spec"].get("network")


def test_example_profile_is_valid():
    assert errors(lambda p: None) == []


def test_control_plane_storage_node_off_the_fabric_is_rejected():
    found = errors(lambda p: p["network"].update(control_plane_on_fabric=False))
    assert len(found) == 2
    assert all("mgmt_ip" in e and "control_plane_on_fabric: true" in e for e in found)


def dual_homed_storage_worker():
    return {
        "site": {"name": "test"},
        "network": {
            "topology": "clos", "calico_mode": "bgp", "control_plane_on_fabric": True, "bgp": {"cluster_asn": 65100},
            "leafs": [{"name": "leaf-a", "asn": 65001, "peer_ip": "10.201.0.1", "fabric_subnet": "10.201.0.0/16"},
                      {"name": "leaf-b", "asn": 65002, "peer_ip": "10.202.0.1", "fabric_subnet": "10.202.0.0/16"}],
        },
        "racks": {"r1": {"nodes": {
            "cp1": {"hostname": "cp1", "mgmt_ip": "192.0.2.10", "local": True, "roles": ["kube_control_plane", "etcd"],
                    "fabric_nics": [{"nic": "eth1", "fabric_ip": "10.201.1.1"}]},
            "w1": {"hostname": "w1", "mgmt_ip": "192.0.2.20", "roles": ["kube_node", "storage"],
                   "fabric_nics": [{"nic": "eth1", "fabric_ip": "10.201.1.2"}, {"nic": "eth2", "fabric_ip": "10.202.1.2"}]},
        }}},
        "storage": {"ceph_public_network": "10.202.0.0/16", "nodes": [{"name": "w1", "devices": ["nvme1n1"]}]},
    }


def test_worker_storage_node_whose_first_nic_is_outside_the_public_network():
    assert _mod.rack_profile_validate(dual_homed_storage_worker())["errors"] == [
        "storage node 'w1' node IP 10.201.1.2 (its first fabric NIC) is outside ceph_public_network "
        "10.202.0.0/16 — list the NIC in that network first"]


def test_a_node_ceph_fabric_nic_is_ignored_with_a_warning():
    profile = dual_homed_storage_worker()
    profile["storage"]["ceph_public_network"] = "10.201.0.0/16"
    assert not any("ceph_fabric_nic" in w for w in _mod.rack_profile_validate(copy.deepcopy(profile))["warnings"])
    profile["racks"]["r1"]["nodes"]["w1"]["ceph_fabric_nic"] = "eth2"
    result = _mod.rack_profile_validate(profile)
    assert result["errors"] == []
    assert any("'ceph_fabric_nic' (node w1) is obsolete and ignored" in w for w in result["warnings"]), result
    profile["storage"]["ceph_fabric_nic"] = "eth2"
    assert any("'ceph_fabric_nic' (storage, node w1) is obsolete" in w
               for w in _mod.rack_profile_validate(profile)["warnings"])


def test_rook_gets_both_networks_as_address_ranges():
    assert ceph_network(rook_ceph_public_network="10.201.0.0/16", rook_ceph_cluster_network="10.202.0.0/16") == {
        "provider": "host", "addressRanges": {"public": ["10.201.0.0/16"], "cluster": ["10.202.0.0/16"]}}


def test_public_network_alone_is_still_applied():
    assert ceph_network(rook_ceph_public_network="10.201.0.0/16", rook_ceph_cluster_network="") == {
        "provider": "host", "addressRanges": {"public": ["10.201.0.0/16"]}}


def test_no_networks_leaves_rook_defaults():
    assert ceph_network(rook_ceph_public_network="", rook_ceph_cluster_network="") is None
