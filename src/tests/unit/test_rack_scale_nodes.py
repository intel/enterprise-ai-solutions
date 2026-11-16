# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Unit tests for roles/fabric_inventory/files/rack_scale_nodes.py: the nodes
# an edited rack-profile.yaml declares, and what the edit changes for the
# nodes already in the inventory.
#
#   python3 -m pytest src/tests/unit

import copy
import subprocess
import sys

import pytest
import yaml

from plugin_loader import CLOS_PROFILE as CLOS, ROOT, write_rack_inventory

SCRIPT = ROOT / "roles" / "fabric_inventory" / "files" / "rack_scale_nodes.py"


def with_node(profile, name, **node):
    out = copy.deepcopy(profile)
    out["racks"]["r1"]["nodes"][name] = node
    return out


GPU_WORKER = {"mgmt_ip": "172.16.0.3", "roles": ["kube_node"], "node_family": "gpu-b300", "accelerator": "nvidia-b300-sxm", "gpu_count": 8,
              "fabric_nics": [{"nic": "ens1", "fabric_ip": "10.201.1.3", "leaf": "leaf1"}]}


def report(tmp_path, profile):
    (tmp_path / "rack-profile.yaml").write_text(yaml.safe_dump(profile))
    result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "rack-profile.yaml"), str(tmp_path / "inv")],
                            capture_output=True, text=True, timeout=60)
    lines = [line.split("\t") for line in result.stdout.splitlines()]
    return result, lines


@pytest.fixture
def installed(tmp_path):
    """The inventory an install of CLOS left."""
    write_rack_inventory(copy.deepcopy(CLOS), tmp_path / "inv")
    return tmp_path


def test_lists_the_nodes_with_their_groups_and_ssh_details(installed):
    result, lines = report(installed, with_node(CLOS, "gpu1", **GPU_WORKER))
    assert result.returncode == 0, result.stderr
    assert lines[0] == ["rack", "clos"]
    nodes = {line[1]: line[2:] for line in lines if line[0] == "node"}
    assert nodes == {
        "cp1": ["ceph_storage_nodes,etcd,kube_control_plane", "172.16.0.1", "ops", "~/.ssh/rack"],
        "w1": ["ecmp_nodes,kube_node", "172.16.0.2", "ops", "~/.ssh/rack"],
        "gpu1": ["gpu_nodes,kube_node", "172.16.0.3", "ops", "~/.ssh/rack"],
    }


@pytest.mark.parametrize("profile", [
    with_node(CLOS, "gpu1", **GPU_WORKER),
    CLOS,
])
def test_adding_a_worker_changes_nothing_for_the_others(installed, profile):
    result, lines = report(installed, profile)
    assert result.returncode == 0, result.stderr
    assert [line for line in lines if line[0] == "changed"] == []


def test_removing_a_worker_changes_nothing_for_the_others(installed):
    profile = copy.deepcopy(CLOS)
    del profile["racks"]["r1"]["nodes"]["w1"]
    result, lines = report(installed, profile)
    assert [line for line in lines if line[0] == "changed"] == []
    assert [line[1] for line in lines if line[0] == "node"] == ["cp1"]


def edit(path, value):
    def apply(profile):
        target = profile
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        return profile
    return apply


@pytest.mark.parametrize("change, expected", [
    (edit(["network", "mtu"], 1500), {"cp1: netplan or BGP peer files", "w1: netplan or BGP peer files", "Calico settings"}),
    (edit(["racks", "r1", "nodes", "w1", "roles"], ["kube_node", "gnr_ap"]), {"w1: groups ecmp_nodes,kube_node -> ecmp_nodes,gnr_ap,kube_node"}),
    (edit(["racks", "r1", "nodes", "w1", "mgmt_ip"], "172.16.0.9"), {"w1: ansible_host"}),
    (edit(["network", "leafs", 1, "asn"], 65009), {"w1: netplan or BGP peer files"}),
    (edit(["network", "pod_cidr"], "10.250.0.0/16"), {"Calico settings"}),
    (edit(["storage", "nodes"], [{"name": "cp1", "devices": ["nvme2n1"]}]), {"cp1: devices", "Ceph settings"}),
])
def test_an_edit_to_the_nodes_that_stay_is_reported(installed, change, expected):
    profile = change(with_node(CLOS, "gpu1", **GPU_WORKER))
    result, lines = report(installed, profile)
    assert result.returncode == 0, result.stderr
    assert {line[1] for line in lines if line[0] == "changed"} == expected


@pytest.mark.parametrize("text", ["racks: [1]\n", "site: [\n", "", "racks: {}\n"])
def test_a_profile_that_does_not_parse_exits_2(installed, text):
    (installed / "rack-profile.yaml").write_text(text)
    result = subprocess.run([sys.executable, str(SCRIPT), str(installed / "rack-profile.yaml"), str(installed / "inv")],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 2
    assert result.stdout == "" and result.stderr.strip()


def test_a_missing_profile_exits_2(tmp_path):
    result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / "none.yaml"), str(tmp_path)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 2 and "No such file" in result.stderr


@pytest.mark.parametrize("path", ["hosts.yaml", "metadata/bgp-peers/w1-leaf1.yaml"])
def test_an_inventory_file_that_does_not_parse_exits_2(installed, path):
    (installed / "inv" / path).write_text("spec: [\n")
    result, _ = report(installed, CLOS)
    assert result.returncode == 2
    assert result.stdout == "" and result.stderr.strip()


def test_the_inventory_pins_the_interpreter_without_counting_as_an_edit(installed):
    # A rack installed before the pin must not read as changed.
    hosts = installed / "inv" / "hosts.yaml"
    inventory = yaml.safe_load(hosts.read_text())
    assert inventory["all"].pop("vars") == {"ansible_python_interpreter": "/usr/bin/python3"}
    hosts.write_text(yaml.safe_dump(inventory))
    result, lines = report(installed, with_node(CLOS, "gpu1", **GPU_WORKER))
    assert result.returncode == 0, result.stderr
    assert [line for line in lines if line[0] == "changed"] == []
