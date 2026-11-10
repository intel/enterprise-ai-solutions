# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import copy
import importlib.util
import ipaddress
import pathlib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("rack_profile_parser", _ROOT / "filter_plugins" / "rack_profile_parser.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
overlaps = _mod.metallb_range_overlaps
STAR = yaml.safe_load((_ROOT / "configs" / "defaults" / "rack-profile.yaml.example").read_text())
CLOS = {
    "site": {"name": "test"},
    "network": {
        "topology": "clos", "calico_mode": "bgp", "fabric_prefix": 16, "control_plane_on_fabric": True,
        "leafs": [
            {"name": "leaf1", "asn": 65001, "peer_ip": "10.201.0.1", "fabric_subnet": "10.201.0.0/16"},
            {"name": "leaf2", "asn": 65002, "peer_ip": "10.202.0.1", "fabric_subnet": "10.202.0.0/16"},
        ],
        "bgp": {"cluster_asn": 65100},
    },
    "racks": {"rack-1": {"nodes": {
        "cp1": {"hostname": "cp1", "mgmt_ip": "10.0.0.10", "roles": ["kube_control_plane", "etcd"],
                "fabric_nics": [{"nic": "ens1", "fabric_ip": "10.201.1.1", "leaf": "leaf1"}]},
        "w1": {"hostname": "w1", "mgmt_ip": "10.0.0.20", "roles": ["kube_node"],
               "fabric_nics": [{"nic": "ens1", "fabric_ip": "10.201.2.1", "leaf": "leaf1"}]},
    }}},
}


def fabric_cidrs(profile):
    result = _mod.rack_profile_parse(copy.deepcopy(profile))
    assert not result["errors"], result["errors"]
    return result["summary"]["fabric_cidrs"]


def test_range_forms():
    cidrs = ["10.201.1.0/28"]
    assert overlaps(cidrs, "10.201.1.5") == cidrs
    assert overlaps(cidrs, "10.201.1.0/24") == cidrs
    assert overlaps(cidrs, "10.201.0.250-10.201.1.2") == cidrs
    assert overlaps(cidrs, "10.201.1.16-10.201.1.20") == []
    assert overlaps(cidrs, "172.16.0.240-172.16.0.250") == []
    assert overlaps(cidrs, "not-an-address") == []


def test_clos_leaf_subnet_without_nodes_counts_as_fabric():
    cidrs = fabric_cidrs(CLOS)
    assert cidrs == ["10.201.0.0/16", "10.202.0.0/16"]
    assert overlaps(cidrs, "10.202.0.50/32") == ["10.202.0.0/16"]


def test_star_gap_between_links_counts_as_fabric():
    profile = copy.deepcopy(STAR)
    nodes = profile["racks"]["rack-01"]["nodes"]
    nodes["master2"]["fabric_nics"][0]["fabric_ip"] = "10.201.1.9"
    nodes["worker1"]["fabric_nics"][1]["fabric_ip"] = "10.201.1.10"
    cidrs = fabric_cidrs(profile)
    assert cidrs == ["10.201.1.0/28"]
    assert overlaps(cidrs, "10.201.1.5/32") == cidrs


def test_invalid_leaf_subnet_is_a_profile_error():
    profile = copy.deepcopy(CLOS)
    profile["network"]["leafs"][1]["fabric_subnet"] = "10.202.0.0/166"
    found = _mod.rack_profile_validate(profile)["errors"]
    assert len(found) == 1 and "leafs[1] ('leaf2') fabric_subnet" in found[0]


def test_merged_networks_drop_subnets_and_sort_ipv4_first():
    nets = [ipaddress.ip_network(n) for n in ("fd00::/64", "10.202.0.0/16", "10.201.1.0/24", "10.201.0.0/16", "fd00::/48")]
    assert _mod._merge_networks(nets) == ["10.201.0.0/16", "10.202.0.0/16", "fd00::/48"]
