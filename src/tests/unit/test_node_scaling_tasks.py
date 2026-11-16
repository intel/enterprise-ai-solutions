# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
"""The rack-scale add-node/remove-node task expressions, evaluated from the
task files: which BGPPeers are deleted, which nodes get the GPU stack."""
import pathlib

import pytest
import yaml

from ansible_render import render

_ROLES = pathlib.Path(__file__).resolve().parents[3] / "roles"


def task(path, name):
    """The task called name, in blocks too."""
    def walk(tasks):
        for t in tasks or []:
            if t.get("name") == name:
                return t
            found = walk(t.get("block")) or walk(t.get("rescue"))
            if found:
                return found
        return None
    found = walk(yaml.safe_load((_ROLES / path).read_text()))
    assert found, name
    return found


def peer(name, node=None):
    return {"metadata": {"name": name}, "spec": {"node": node} if node else {}}


def test_only_the_removed_nodes_bgp_peers_are_deleted():
    loop = task("fabric_bgp/tasks/remove-node.yaml",
                "fabric_bgp | remove-node | Delete the removed node(s)' BGPPeers")["loop"]
    peers = [peer("w2-leaf1", "w2"), peer("w2-leaf2", "w2"), peer("w1-leaf1", "w1"), peer("global")]
    selected = render(loop, {"_bgp_peers": {"resources": peers}, "_removed_nodes_list": ["w2"]})
    assert [p["metadata"]["name"] for p in selected] == ["w2-leaf1", "w2-leaf2"]


@pytest.mark.parametrize("groups, expected", [({"gpu_nodes": ["g1"]}, ["g1"]), ({}, [])])
def test_only_new_gpu_nodes_get_the_gpu_stack(groups, expected):
    expr = task("nvidia_gpu_operator/tasks/add-node.yaml",
                "nvidia_gpu_operator | add-node | New GPU node(s)")["ansible.builtin.set_fact"]["_gpu_added"]
    assert render(expr, {"_added_nodes_list": ["w2", "g1"], "groups": groups}) == expected


@pytest.mark.parametrize("own_nfd, shared_nfd, expected", [
    (False, {"status": {"status": "deployed"}}, True),
    (False, {"status": None}, False),
    (True, {"status": None}, True),
])
def test_the_operator_can_be_installed_with_either_nfd(own_nfd, shared_nfd, expected):
    expr = task("nvidia_gpu_operator/tasks/add-node.yaml",
                "nvidia_gpu_operator | add-node | Releases found")["ansible.builtin.set_fact"]["_gpu_nfd_available"]
    assert render(expr, {"nvidia_gpu_operator_nfd_enabled": own_nfd, "_gpu_nfd_release": shared_nfd}) == expected


@pytest.mark.parametrize("allocatable, gpu_count, fails", [
    ({"nvidia.com/gpu": "8"}, 8, False),
    ({"nvidia.com/gpu": "4"}, 8, True),
    ({}, 8, True),
    ({"nvidia.com/gpu": "1"}, None, False),
])
def test_a_new_node_fails_until_all_its_gpus_are_allocatable(allocatable, gpu_count, fails):
    t = task("nvidia_gpu_operator/tasks/add-node.yaml",
             "nvidia_gpu_operator | add-node | Fail if a new node's GPUs are not allocatable")
    node_vars = {"node_gpu_count": gpu_count} if gpu_count else {}
    variables = {"item": {"item": "g1", "resources": [{"status": {"allocatable": allocatable}}]},
                 "hostvars": {"g1": node_vars}}
    for name, value in t["vars"].items():  # in order: each may use the ones before
        variables[name] = render(value, variables)
    assert render("{{ %s }}" % t["when"][1], variables) == fails


@pytest.mark.parametrize("scope, expected", [(["w2"], ["w2"]), ([], []), (None, ["cp1", "w1", "w2"])])
def test_the_fabric_teardown_takes_down_the_scoped_nodes_only(scope, expected):
    expr = task("fabric_netplan/tasks/teardown.yaml",
                "fabric_netplan | teardown | Compute cluster node list")["ansible.builtin.set_fact"]["_fabric_all_nodes"]
    variables = {"groups": {"kube_control_plane": ["cp1"], "kube_node": ["w1", "w2"]}}
    if scope is not None:
        variables["_fabric_scope_nodes"] = scope
    assert sorted(render(expr, variables)) == expected


def test_add_node_refuses_a_rack_node_before_it_enters_the_inventory():
    names = [t.get("name", "") for t in yaml.safe_load((_ROLES / "kubernetes/tasks/add-node.yaml").read_text())]
    reload = names.index("kubernetes | add-node | Reload inventory to pick up newly added node(s)")
    reach = names.index("kubernetes | add-node | Rack-scale | Reach the new node(s) over SSH")
    refusals = [names.index("kubernetes | add-node | SSH pre-flight | Fail on already-joined node(s)"),
                names.index("kubernetes | add-node | SSH pre-flight | Refuse new node(s) with Docker installed")]
    written = names.index("kubernetes | add-node | Rack-scale fabric")
    assert reload < reach < min(refusals) and max(refusals) < written  # a reload drops in-memory hosts
    inventory = task("kubernetes/tasks/add-node.yaml",
                     "kubernetes | add-node | Rack-scale | Inventory and fabric files from rack-profile.yaml")
    assert inventory["ansible.builtin.include_role"]["name"] == "fabric_inventory"
    assert inventory in task("kubernetes/tasks/add-node.yaml", "kubernetes | add-node | Rack-scale fabric")["block"]


@pytest.mark.parametrize("only, expected", [(["w2"], ["w2"]), (None, ["cp1", "w1", "w2"])])
def test_the_new_rack_nodes_are_registered_with_their_groups(only, expected):
    register = task("fabric_inventory/tasks/register_hosts.yaml",
                    "fabric_inventory | Register cluster nodes into running inventory")
    hosts_yaml = yaml.safe_dump({"all": {
        "hosts": {n: {"ansible_host": "172.16.0.%d" % i, "ansible_user": "ops"} for i, n in enumerate(["cp1", "w1", "w2"], 1)},
        "children": {"kube_control_plane": {"hosts": {"cp1": None}}, "kube_node": {"hosts": {"w1": None, "w2": None}}}}})
    variables = {"_rack_parsed": {"hosts_yaml": hosts_yaml}}
    if only is not None:
        variables["_fabric_register_hosts"] = only
    parse = task("fabric_inventory/tasks/register_hosts.yaml", "fabric_inventory | Generated inventory")
    variables["_generated_inv"] = render(parse["ansible.builtin.set_fact"]["_generated_inv"], variables)
    items = render(register["loop"], variables)
    assert [item["key"] for item in items] == expected
    variables["item"] = items[-1]
    assert sorted(render(register["ansible.builtin.add_host"]["groups"], variables)) == ["all", "k8s_cluster", "kube_node"]
    assert render(register["ansible.builtin.add_host"]["ansible_host"], variables) == "172.16.0.3"


def test_the_monitoring_crd_is_looked_up_in_its_api_group():
    lookup = task("nvidia_gpu_operator/tasks/add-node.yaml",
                  "nvidia_gpu_operator | add-node | Find the ServiceMonitor CRD")["kubernetes.core.k8s_info"]
    assert lookup["api_version"] == "apiextensions.k8s.io/v1"


@pytest.mark.parametrize("crd, installs", [
    ({"skipped": True}, False),
    ({"resources": []}, False),
    ({"resources": [{"metadata": {"name": "servicemonitors.monitoring.coreos.com"}}]}, True),
])
def test_the_gpu_dashboards_need_observability(crd, installs):
    when = task("nvidia_gpu_operator/tasks/add-node.yaml",
                "nvidia_gpu_operator | add-node | GPU dashboards and alerts")["when"]
    assert render("{{ %s }}" % when, {"_gpu_monitoring_crd": crd}) == installs
