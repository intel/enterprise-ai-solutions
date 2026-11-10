# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import copy
import importlib.util
import pathlib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("rack_profile_parser", _ROOT / "filter_plugins" / "rack_profile_parser.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
rack_profile_parse = _mod.rack_profile_parse


def nic(name, ip, leaf=None):
    return {"nic": name, "fabric_ip": ip, **({"leaf": leaf} if leaf else {})}


CLOS = {
    "site": {"name": "test"},
    "network": {
        "topology": "clos", "calico_mode": "bgp", "control_plane_on_fabric": True,
        "leafs": [{"name": "leaf1", "asn": 65001, "peer_ip": "10.201.0.1", "fabric_subnet": "10.201.0.0/16"},
                  {"name": "leaf2", "asn": 65002, "peer_ip": "10.202.0.1", "fabric_subnet": "10.202.0.0/16"}],
        "bgp": {"cluster_asn": 65100},
    },
    "racks": {"r1": {"nodes": {
        "cp1": {"hostname": "cp1", "mgmt_ip": "172.16.0.1", "local": True, "roles": ["kube_control_plane", "etcd"],
                "fabric_nics": [nic("ens1", "10.201.1.1", "leaf1")]},
        "w1": {"hostname": "w1", "mgmt_ip": "172.16.0.2", "roles": ["kube_node"],
               "fabric_nics": [nic("ens1", "10.201.1.2", "leaf1"), nic("ens2", "10.202.1.2", "leaf2")]},
    }}},
}


def routes(profile):
    parsed = rack_profile_parse(copy.deepcopy(profile))
    assert parsed["errors"] == []
    out = {}
    for fname, content in parsed["netplan_files"].items():
        node = fname.rsplit("/", 1)[-1].removesuffix(".yaml")
        for name, eth in yaml.safe_load(content)["network"]["ethernets"].items():
            out[(node, name)] = eth.get("routes", [])
    return out


def test_dual_homed_cross_leaf_routes_are_backups():
    r = routes(CLOS)
    assert r[("w1", "ens1")] == [{"to": "10.202.0.0/16", "via": "10.201.0.1", "metric": 200}]
    assert r[("w1", "ens2")] == [{"to": "10.201.0.0/16", "via": "10.202.0.1", "metric": 200}]


def test_single_homed_route_keeps_the_default_metric():
    assert routes(CLOS)[("cp1", "ens1")] == [{"to": "10.202.0.0/16", "via": "10.201.0.1"}]


def test_l2_has_no_routes():
    l2 = copy.deepcopy(CLOS)
    l2["network"] = {"topology": "l2", "calico_mode": "vxlan", "control_plane_on_fabric": True}
    for node in l2["racks"]["r1"]["nodes"].values():
        node["fabric_nics"] = [{k: v for k, v in fn.items() if k != "leaf"} for fn in node["fabric_nics"][:1]]
    assert all(not r for r in routes(l2).values())


def test_star_routes_have_no_metric():
    star = yaml.safe_load((_ROOT / "configs" / "defaults" / "rack-profile.yaml.example").read_text())
    assert star["network"]["topology"] == "star"
    spoke_routes = [route for r in routes(star).values() for route in r]
    assert spoke_routes and all("metric" not in route for route in spoke_routes)
