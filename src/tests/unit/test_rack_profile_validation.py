# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import copy

import pytest
import yaml

from plugin_loader import EXAMPLE_PROFILE as EXAMPLE, load_plugin

_mod = load_plugin("rack_profile_parser")


def clos():
    return {
        "site": {"name": "test"},
        "network": {
            "topology": "clos", "calico_mode": "bgp", "fabric_prefix": 24, "control_plane_on_fabric": True,
            "bgp": {"cluster_asn": 65100},
            "leafs": [
                {"name": "leaf-a", "asn": 65001, "peer_ip": "10.10.1.1", "fabric_subnet": "10.10.1.0/24"},
                {"name": "leaf-b", "asn": 65002, "peer_ip": "10.10.2.1", "fabric_subnet": "10.10.2.0/24"},
            ],
        },
        "racks": {"rack-01": {"nodes": {
            "cp1": {"hostname": "cp1", "mgmt_ip": "192.0.2.10", "roles": ["kube_control_plane", "etcd"],
                    "fabric_nics": [{"nic": "eth1", "fabric_ip": "10.10.1.10"}]},
            "w1": {"hostname": "w1", "mgmt_ip": "192.0.2.20", "roles": ["kube_node"],
                   "fabric_nics": [{"nic": "eth1", "fabric_ip": "10.10.1.20"},
                                   {"nic": "eth2", "fabric_ip": "10.10.2.20"}]},
        }}},
    }


def none_topology(ceph_network):
    nodes = {
        "cp1": {"hostname": "cp1", "mgmt_ip": "192.0.2.10", "roles": ["kube_control_plane", "etcd", "storage"]},
        "w1": {"hostname": "w1", "mgmt_ip": "192.0.2.20", "roles": ["kube_node"]},
    }
    return {
        "site": {"name": "test"},
        "network": {"topology": "none", "calico_mode": "vxlan"},
        "racks": {"rack-01": {"nodes": nodes}},
        "storage": {"ceph_public_network": ceph_network, "ceph_cluster_network": ceph_network,
                    "nodes": [{"name": "cp1", "devices": ["nvme1n1"]}]},
    }


def validate(profile):
    return _mod.rack_profile_validate(copy.deepcopy(profile))


def nic(profile, node, index):
    return profile["racks"]["rack-01"]["nodes"][node]["fabric_nics"][index]


def test_clos_profile_without_leaf_names_is_valid():
    assert validate(clos())["errors"] == []


def test_clos_leaf_is_set_from_the_nic_subnet():
    profile = clos()
    nodes = profile["racks"]["rack-01"]["nodes"]
    assert _mod._assign_clos_leafs(nodes, profile["network"]["leafs"]) == []
    assert [fn["leaf"] for fn in nodes["w1"]["fabric_nics"]] == ["leaf-a", "leaf-b"]


def test_named_leaf_must_hold_the_nic_address():
    profile = clos()
    nic(profile, "w1", 0)["leaf"] = "leaf-b"
    assert validate(profile)["errors"] == [
        "node 'w1' NIC 'eth1' fabric_ip 10.10.1.20 is not within leaf-b's fabric_subnet 10.10.2.0/24"]


def test_overlapping_leaf_subnets_are_rejected():
    profile = clos()
    profile["network"]["leafs"][1]["fabric_subnet"] = "10.10.0.0/16"
    found = validate(profile)["errors"]
    assert len(found) == 1 and found[0].startswith("leaf subnets overlap: leaf-a 10.10.1.0/24 and leaf-b 10.10.0.0/16")


def test_address_in_no_leaf_subnet_names_the_tbd_leaf():
    profile = clos()
    profile["network"]["leafs"][1]["fabric_subnet"] = "TBD"
    found = validate(profile)["errors"]
    assert len(found) == 1
    assert "fabric_ip 10.10.2.20 is in no leaf's fabric_subnet (leaf-a 10.10.1.0/24)" in found[0]
    assert "fabric_subnet is TBD for leaf-b" in found[0]


def test_unknown_leaf_is_an_error_on_clos_and_a_warning_elsewhere():
    profile = clos()
    nic(profile, "w1", 0)["leaf"] = "leaf-x"
    assert any("references unknown leaf 'leaf-x'" in e for e in validate(profile)["errors"])
    star = copy.deepcopy(EXAMPLE)
    star["racks"]["rack-01"]["nodes"]["worker1"]["fabric_nics"][0]["leaf"] = "leaf-x"
    result = validate(star)
    assert result["errors"] == []
    assert any("references unknown leaf 'leaf-x'" in w for w in result["warnings"])


def test_l2_address_outside_the_shared_segment():
    profile = clos()
    profile["network"].update(topology="l2", calico_mode="vxlan", leafs=[])
    del profile["racks"]["rack-01"]["nodes"]["w1"]["fabric_nics"][1]
    nic(profile, "cp1", 0)["fabric_ip"] = "10.10.9.10"
    assert validate(profile)["errors"] == [
        "node 'cp1' NIC 'eth1' fabric_ip 10.10.9.10 is outside the L2 segment 10.10.1.0/24 "
        "— every node on an L2 fabric shares one subnet"]


def test_every_node_needs_mgmt_ip_and_roles():
    def without(field):
        profile = copy.deepcopy(EXAMPLE)
        del profile["racks"]["rack-01"]["nodes"]["worker1"][field]
        return validate(profile)["errors"]
    assert any("node 'worker1' is missing required field 'mgmt_ip'" in e for e in without("mgmt_ip"))
    assert any("node 'worker1' has no roles" in e for e in without("roles"))


def test_topology_none_runs_ceph_on_the_management_network():
    assert validate(none_topology("192.0.2.0/24"))["errors"] == []
    assert validate(none_topology("10.99.0.0/16"))["errors"] == [
        "storage node 'cp1' mgmt_ip 192.0.2.10 is outside ceph_public_network 10.99.0.0/16 "
        "— with no fabric NIC, Ceph runs on the management network",
        "storage node 'cp1' mgmt_ip 192.0.2.10 is outside ceph_cluster_network 10.99.0.0/16 "
        "— with no fabric NIC, Ceph runs on the management network"]


def test_node_ip_is_the_one_hosts_yaml_writes():
    nodes = EXAMPLE["racks"]["rack-01"]["nodes"]
    assert _mod._node_ip(nodes["master1"], True) == "10.201.1.1"
    assert _mod._node_ip(nodes["master1"], False) == "10.0.0.10"
    assert _mod._node_ip(nodes["worker1"], False) == "10.201.1.2"
    assert _mod._node_ip({"mgmt_ip": "192.0.2.5", "roles": ["kube_node"]}, True) == "192.0.2.5"
    hosts = yaml.safe_load(_mod.rack_profile_parse(copy.deepcopy(EXAMPLE))["hosts_yaml"])["all"]["hosts"]
    assert {name: h["ip"] for name, h in hosts.items()} == {
        name: _mod._node_ip(node, True) for name, node in nodes.items()}


def calico_mtu(profile):
    calico = yaml.safe_load(_mod.rack_profile_parse(copy.deepcopy(profile))["calico_yml"])
    return calico["calico_mtu"]


def test_topology_none_defaults_to_the_management_network_mtu():
    profile = none_topology("192.0.2.0/24")
    assert calico_mtu(profile) == 1500 - 50
    profile["network"]["mtu"] = 9000
    assert calico_mtu(profile) == 9000 - 50
    assert calico_mtu(clos()) == 9000


@pytest.mark.parametrize("mtu, error", [("1500", True), (40, True), (True, True), (1500.0, True), (1279, True),
                                        (90000, True), (1280, False), (9702, False)])
def test_network_mtu_must_be_a_whole_number_from_1280_to_65535(mtu, error):
    profile = none_topology("192.0.2.0/24")
    profile["network"].update(mtu=mtu, vxlan_overhead=0)
    errors = validate(profile)["errors"]
    assert any("network.mtu must be a whole number from 1280 to 65535" in e for e in errors) == error


def test_a_bad_mtu_does_not_hide_the_other_errors():
    profile = none_topology("192.0.2.0/24")
    profile["network"].update(mtu="9000", calico_mode="bgp")
    errors = validate(profile)["errors"]
    assert any("network.mtu" in e for e in errors) and any("calico_mode must be" in e for e in errors)


@pytest.mark.parametrize("overhead, mtu, error", [
    ("50", 1500, ["vxlan_overhead must be"]), (-1, 1500, ["vxlan_overhead must be"]),
    (300, 1500, ["Calico's MTU, must be at least 1280"]),
    (300, "9000", ["network.mtu must be"]),  # no second error from the default it fell back to
    (50, 1500, [])])
def test_the_vxlan_overhead_must_leave_calico_a_valid_mtu(overhead, mtu, error):
    profile = none_topology("192.0.2.0/24")
    profile["network"].update(vxlan_overhead=overhead, mtu=mtu)
    errors = [e for e in validate(profile)["errors"] if "mtu" in e or "overhead" in e]
    assert [next(x for x in error if x in e) for e in errors] == error


def test_bgp_has_no_vxlan_overhead_floor():
    profile = clos()
    profile["network"].update(vxlan_overhead=300, mtu=1500)
    assert not [e for e in validate(profile)["errors"] if "Calico's MTU" in e]


def test_calico_mode_must_be_vxlan_or_bgp():
    profile = none_topology("192.0.2.0/24")
    profile["network"]["calico_mode"] = "VXLAN"
    assert "network.calico_mode must be 'vxlan' or 'bgp', got 'VXLAN'" in validate(profile)["errors"]
