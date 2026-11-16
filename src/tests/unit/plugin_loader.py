# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Setup shared by the filter-plugin unit tests."""
import copy
import importlib.util
import pathlib

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[3]
EXAMPLE_PROFILE = yaml.safe_load((ROOT / "configs" / "defaults" / "rack-profile.yaml.example").read_text())


def nic(name, ip, leaf):
    return {"nic": name, "fabric_ip": ip, "leaf": leaf}


CLOS_PROFILE = {
    "site": {"name": "test"},
    "network": {
        "topology": "clos", "calico_mode": "bgp", "control_plane_on_fabric": True,
        "leafs": [{"name": "leaf1", "asn": 65001, "peer_ip": "10.201.0.1", "fabric_subnet": "10.201.0.0/16"},
                  {"name": "leaf2", "asn": 65002, "peer_ip": "10.202.0.1", "fabric_subnet": "10.202.0.0/16"}],
    },
    "access": {"ansible_user": "ops", "ansible_ssh_private_key_file": "~/.ssh/rack"},
    "racks": {"r1": {"nodes": {
        "cp1": {"hostname": "host-cp1", "mgmt_ip": "172.16.0.1", "roles": ["kube_control_plane", "etcd", "storage"],
                "fabric_nics": [nic("ens1", "10.201.1.1", "leaf1")]},
        "w1": {"mgmt_ip": "172.16.0.2", "roles": ["kube_node"],
               "fabric_nics": [nic("ens1", "10.201.1.2", "leaf1"), nic("ens2", "10.202.1.2", "leaf2")]},
    }}},
    "storage": {"nodes": [{"name": "cp1", "devices": ["nvme1n1"]}]},
}


def load_plugin(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "filter_plugins" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def edited(profile, add=(), remove=(), **network):
    """A copy of <profile> with nodes (name, node) added to rack r1, nodes removed, and network keys set."""
    out = copy.deepcopy(profile)
    nodes = out["racks"]["r1"]["nodes"]
    nodes.update(dict(add))
    for name in remove:
        del nodes[name]
    out["network"].update(network)
    return out


def write_rack_inventory(profile, inventory_dir):
    """Write what fabric_inventory's install writes for <profile> into <inventory_dir>."""
    parsed = load_plugin("rack_profile_parser").rack_profile_parse(profile)
    assert parsed["errors"] == [], parsed["errors"]
    files = {"hosts.yaml": parsed["hosts_yaml"],
             "metadata/k8s-net-calico.yml": parsed["calico_yml"],
             "metadata/vars/ceph-storage.yml": parsed["ceph_yml"],
             **parsed["netplan_files"], **parsed["bgp_peer_files"]}
    for rel, content in files.items():
        path = pathlib.Path(inventory_dir) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
