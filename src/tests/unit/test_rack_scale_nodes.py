# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Unit tests for roles/fabric_inventory/files/rack_scale_nodes.py: the nodes
# an edited rack-profile.yaml declares, and what the edit changes for the
# nodes already in the inventory.
#
#   python3 -m pytest src/tests/unit

import copy
import importlib.util
import subprocess
import sys

import pytest
import yaml
from ansible.inventory.manager import InventoryManager
from ansible.parsing.dataloader import DataLoader
from ansible.vars.manager import VariableManager

from plugin_loader import CLOS_PROFILE as CLOS, ROOT, edited, nic, write_rack_inventory

SCRIPT = ROOT / "roles" / "fabric_inventory" / "files" / "rack_scale_nodes.py"


GPU_WORKER = {"mgmt_ip": "172.16.0.3", "roles": ["kube_node"], "node_family": "gpu-b300", "accelerator": "nvidia-b300-sxm", "gpu_count": 8,
              "fabric_nics": [nic("ens1", "10.201.1.3", "leaf1")]}


def run_script(profile, inventory_dir, cluster=("cp1", "w1")):
    return subprocess.run([sys.executable, str(SCRIPT), str(profile), str(inventory_dir), *cluster],
                          capture_output=True, text=True, timeout=60)


def report(tmp_path, profile, cluster=("cp1", "w1")):
    (tmp_path / "rack-profile.yaml").write_text(yaml.safe_dump(profile))
    result = run_script(tmp_path / "rack-profile.yaml", tmp_path / "inv", cluster)
    lines = [line.split("\t") for line in result.stdout.splitlines()]
    return result, lines


@pytest.fixture
def installed(tmp_path):
    """The inventory an install of CLOS left."""
    write_rack_inventory(copy.deepcopy(CLOS), tmp_path / "inv")
    return tmp_path


def test_lists_the_nodes_with_their_groups_and_ssh_details(installed):
    result, lines = report(installed, edited(CLOS, add=[("gpu1", GPU_WORKER)]))
    assert result.returncode == 0, result.stderr
    assert lines[0] == ["rack", "clos"]
    nodes = {line[1]: line[2:] for line in lines if line[0] == "node"}
    assert nodes == {
        "cp1": ["ceph_storage_nodes,etcd,kube_control_plane", "172.16.0.1", "ops", "~/.ssh/rack"],
        "w1": ["ecmp_nodes,kube_node", "172.16.0.2", "ops", "~/.ssh/rack"],
        "gpu1": ["gpu_nodes,kube_node", "172.16.0.3", "ops", "~/.ssh/rack"],
    }


@pytest.mark.parametrize("profile", [
    edited(CLOS, add=[("gpu1", GPU_WORKER)]),
    CLOS,
])
def test_adding_a_worker_changes_nothing_for_the_others(installed, profile):
    result, lines = report(installed, profile)
    assert result.returncode == 0, result.stderr
    assert [line for line in lines if line[0] == "changed"] == []


def test_removing_a_worker_changes_nothing_for_the_others(installed):
    result, lines = report(installed, edited(CLOS, remove=["w1"]))
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
    profile = change(edited(CLOS, add=[("gpu1", GPU_WORKER)]))
    result, lines = report(installed, profile)
    assert result.returncode == 0, result.stderr
    assert {line[1] for line in lines if line[0] == "changed"} == expected


@pytest.mark.parametrize("text", ["racks: [1]\n", "site: [\n", "", "racks: {}\n"])
def test_a_profile_that_does_not_parse_exits_2(installed, text):
    (installed / "rack-profile.yaml").write_text(text)
    result = run_script(installed / "rack-profile.yaml", installed / "inv")
    assert result.returncode == 2
    assert result.stdout == "" and result.stderr.strip()


def test_a_missing_profile_exits_2(tmp_path):
    result = run_script(tmp_path / "none.yaml", tmp_path)
    assert result.returncode == 2 and "No such file" in result.stderr


def test_a_missing_inventory_exits_2(installed):
    (installed / "inv" / "hosts.yaml").unlink()
    result, _ = report(installed, CLOS)
    assert result.returncode == 2 and "No such file" in result.stderr


@pytest.mark.parametrize("path", ["hosts.yaml", "metadata/bgp-peers/w1-leaf1.yaml"])
def test_an_inventory_file_that_does_not_parse_exits_2(installed, path):
    (installed / "inv" / path).write_text("spec: [\n")
    result, _ = report(installed, CLOS)
    assert result.returncode == 2
    assert result.stdout == "" and result.stderr.strip()


def test_the_report_reads_the_inventory_where_fabric_inventory_writes_it():
    defaults = yaml.safe_load((SCRIPT.parents[1] / "defaults" / "main.yaml").read_text())
    def rel(key):
        return defaults[key].replace("{{ env_dir }}/inventory/", "")
    script = importlib.util.spec_from_file_location("rack_scale_nodes", SCRIPT)
    module = importlib.util.module_from_spec(script)
    script.loader.exec_module(module)
    assert [rel_path for _, _, rel_path in module.RACK_FILES] == [rel("rack_scale_calico_path"), rel("rack_scale_ceph_path")]
    assert (module.NETPLAN_DIR, module.BGP_PEERS_DIR) == (rel("rack_scale_netplan_dir"), rel("rack_scale_bgp_peers_dir"))
    assert rel("rack_scale_inventory_path") == "hosts.yaml"


def test_a_bgp_peer_file_of_no_node_counts_as_the_racks(installed):
    (installed / "inv" / "metadata" / "bgp-peers" / "global.yaml").write_text("kind: BGPPeer\nspec: {peerIP: 10.0.0.9}\n")
    result, lines = report(installed, CLOS)
    assert result.returncode == 0, result.stderr
    assert ["changed", "netplan or BGP peer files of no inventory node"] in lines


@pytest.mark.parametrize("cluster, kind", [(("cp1", "w1"), "unfinished"), (("cp1", "w1", "gpu1"), "changed")])
def test_an_edit_to_an_add_node_that_did_not_finish_is_reported_as_such(tmp_path, cluster, kind):
    write_rack_inventory(edited(CLOS, add=[("gpu1", GPU_WORKER)]), tmp_path / "inv")
    fixed = edited(CLOS, add=[("gpu1", {**GPU_WORKER, "fabric_nics": [nic("ens1", "10.201.1.9", "leaf1")]})])
    result, lines = report(tmp_path, fixed, cluster)
    assert result.returncode == 0, result.stderr
    assert [line for line in lines if line[0] in ("changed", "unfinished")] == [
        [kind, "gpu1: access_ip, ip, node_fabric_nics"], [kind, "gpu1: netplan or BGP peer files"]]


ETCD_ONLY = {"mgmt_ip": "172.16.0.6", "roles": ["etcd"], "fabric_nics": [nic("ens1", "10.201.1.6", "leaf1")]}


@pytest.mark.parametrize("change, expected", [
    ({"mgmt_ip": "172.16.0.9"}, "e1: ansible_host"),
    ({"roles": ["etcd", "kube_node"]}, "e1: groups etcd -> etcd,kube_node"),
])
def test_an_edit_to_a_node_that_is_no_kubernetes_node_is_reported(tmp_path, change, expected):
    write_rack_inventory(edited(CLOS, add=[("e1", ETCD_ONLY)]), tmp_path / "inv")
    _, lines = report(tmp_path, edited(CLOS, add=[("e1", {**ETCD_ONLY, **change})]))
    assert ["changed", expected] in lines


def test_the_files_a_removal_left_do_not_count_as_an_edit(installed):
    # A post phase stopped after it rewrote hosts.yaml, before the stale files went.
    write_rack_inventory(edited(CLOS, remove=["w1"]), installed / "inv")
    for leftover in ["netplan/w1.yaml", "metadata/bgp-peers/w1-leaf1.yaml"]:
        assert (installed / "inv" / leftover).is_file()
    result, lines = report(installed, edited(CLOS, remove=["w1"]), cluster=("cp1",))
    assert result.returncode == 0, result.stderr
    assert [line for line in lines if line[0] == "changed"] == []


def test_an_inventory_of_the_wrong_shape_exits_2(installed):
    (installed / "inv" / "hosts.yaml").write_text("all: [1, 2]\n")
    result, _ = report(installed, CLOS)
    assert result.returncode == 2
    assert result.stdout == "" and result.stderr.strip()


def test_the_interpreter_pin_leaves_the_controller_on_the_installers_python(installed):
    # The playbooks run on the implicit localhost: its own interpreter outranks all.vars.
    loader = DataLoader()
    inventory = InventoryManager(loader=loader, sources=[str(installed / "inv" / "hosts.yaml")])
    variables = VariableManager(loader=loader, inventory=inventory)
    assert variables.get_vars(host=inventory.get_host("w1"))["ansible_python_interpreter"] == "/usr/bin/python3"
    assert variables.get_vars(host=inventory.get_host("localhost"))["ansible_python_interpreter"] == sys.executable


def test_the_inventory_pins_the_interpreter_without_counting_as_an_edit(installed):
    hosts = installed / "inv" / "hosts.yaml"
    inventory = yaml.safe_load(hosts.read_text())
    assert inventory["all"].pop("vars") == {"ansible_python_interpreter": "/usr/bin/python3"}
    hosts.write_text(yaml.safe_dump(inventory))
    result, lines = report(installed, edited(CLOS, add=[("gpu1", GPU_WORKER)]))
    assert result.returncode == 0, result.stderr
    assert [line for line in lines if line[0] == "changed"] == []
