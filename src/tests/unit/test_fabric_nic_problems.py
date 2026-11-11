# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import importlib.util
import json
import pathlib

import pytest
import yaml
from ansible.plugins.loader import filter_loader
from ansible_render import render, trust_as_template

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("fabric_nic_problems", _ROOT / "filter_plugins" / "fabric_nic_problems.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
fabric_nic_problems = _mod.fabric_nic_problems

FABRIC = {"ens6f0": {"dhcp4": False, "dhcp6": False, "addresses": ["10.201.1.2/16"], "mtu": 9000}}


def addr(local, prefixlen, scope="global"):
    return {"family": "inet6" if ":" in local else "inet", "local": local, "prefixlen": prefixlen,
            "scope": scope, "valid_life_time": 4294967295, "preferred_life_time": 4294967295}


UP = ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"]
NO_CARRIER = ["NO-CARRIER", "BROADCAST", "MULTICAST", "UP"]
DOWN = ["BROADCAST", "MULTICAST"]


def link(name, *addrs, mtu=9000, master=None, flags=UP, altnames=()):
    """One interface as `ip -j addr show` prints it."""
    out = {"ifindex": 5, "ifname": name, "flags": flags, "mtu": mtu, "link_type": "ether",
           "address": "aa:bb:cc:dd:ee:01", "addr_info": [*addrs, addr("fe80::a8bb:ccff:fedd:ee01", 64, "link")]}
    if master:
        out["master"] = master
    if altnames:
        out["altnames"] = list(altnames)
    return out


LO = {"ifindex": 1, "ifname": "lo", "mtu": 65536, "flags": ["LOOPBACK", "UP", "LOWER_UP"],
      "addr_info": [addr("127.0.0.1", 8, "host"), addr("::1", 128, "host")]}
MGMT = link("eno1", addr("192.0.2.10", 24), mtu=1500)


def problems(links, ethernets=None):
    """(extra, wrong) for ethernets, FABRIC by default."""
    out = fabric_nic_problems(links, FABRIC if ethernets is None else ethernets)
    return out["extra"], out["wrong"]


def test_nic_as_the_fabric_file_sets_it():
    assert problems([LO, MGMT, link("ens6f0", addr("10.201.1.2", 16))]) == ([], [])


def test_an_address_from_another_netplan_file():
    assert problems([link("ens6f0", addr("10.201.1.2", 16), addr("192.168.0.5", 24))]) == (
        ["ens6f0: has 192.168.0.5/24"], [])


def test_a_nic_another_definition_took():
    assert problems([link("ens6f0", addr("192.168.0.5", 24))]) == (
        ["ens6f0: has 192.168.0.5/24"], ["ens6f0: 10.201.1.2/16 is missing"])


def test_the_same_address_with_another_prefix_does_not_count():
    assert problems([link("ens6f0", addr("10.201.1.2", 24))]) == (
        ["ens6f0: has 10.201.1.2/24"], ["ens6f0: 10.201.1.2/16 is missing"])


def test_up_without_carrier_missing_addresses_and_mtu_are_not_reported():
    assert problems([link("ens6f0", mtu=1500, flags=NO_CARRIER)]) == ([], [])
    assert problems([link("ens6f0", flags=NO_CARRIER, master="bond0")]) == ([], ["ens6f0: is in bond0"])
    assert problems([link("ens6f0", addr("192.168.0.5", 24), flags=NO_CARRIER)]) == (["ens6f0: has 192.168.0.5/24"], [])


def test_a_nic_that_is_down_is_missing_its_addresses():
    assert problems([link("ens6f0", flags=DOWN)]) == ([], ["ens6f0: is down", "ens6f0: 10.201.1.2/16 is missing"])


def test_a_nic_named_by_its_altname():
    assert problems([link("ens6f0", addr("10.201.1.2", 16), altnames=["enp42s0f0"])],
                    {"enp42s0f0": FABRIC["ens6f0"]}) == ([], [])


def test_a_nic_that_is_not_there():
    assert problems([LO, MGMT]) == ([], ["ens6f0: no such NIC"])


def test_mtu_and_master():
    assert problems([link("ens6f0", addr("10.201.1.2", 16), mtu=1500, master="vrf1")]) == (
        [], ["ens6f0: MTU is 1500, not 9000", "ens6f0: is in vrf1"])


def test_no_mtu_in_the_file_accepts_any():
    assert problems([link("ens6f0", addr("10.201.1.2", 16), mtu=1500)], {"ens6f0": {"addresses": ["10.201.1.2/16"]}}) == (
        [], [])


def test_only_the_families_the_file_sets_are_compared():
    slaac = addr("2001:db8::a8bb:ccff:fedd:ee01", 64)
    assert problems([link("ens6f0", addr("10.201.1.2", 16), slaac)]) == ([], [])
    dual = {"ens6f0": {"addresses": ["2001:db8:0:0::5/64", "10.201.1.2/16"]}}
    assert problems([link("ens6f0", addr("10.201.1.2", 16), addr("2001:db8::5", 64))], dual) == ([], [])
    assert problems([link("ens6f0", addr("10.201.1.2", 16), addr("2001:db8::5", 64), slaac)], dual) == (
        ["ens6f0: has 2001:db8::a8bb:ccff:fedd:ee01/64"], [])


def test_loopback_host_addresses_are_not_compared():
    assert problems([LO], {"lo": {"addresses": ["10.201.9.1/32"]}}) == ([], ["lo: 10.201.9.1/32 is missing"])


def test_each_fabric_nic_is_checked_and_other_nics_are_not():
    fabric = {"ens6f0": {"addresses": ["10.201.1.2/31"], "mtu": 9000},
              "ens28f0": {"addresses": ["10.201.2.2/31"], "mtu": 9000}}
    links = [LO, MGMT, link("ens6f0", addr("10.201.1.2", 31)),
             link("ens28f0", addr("10.201.2.2", 31), addr("10.201.2.9", 31))]
    assert problems(links, fabric) == (["ens28f0: has 10.201.2.9/31"], [])


def test_a_node_without_fabric_nics():
    assert problems([LO, MGMT], {}) == ([], [])


# The role's tasks, rendered as Ansible does.
_TASKS = yaml.safe_load((_ROOT / "roles/fabric_netplan/tasks/check_nics.yaml").read_text())
_BLOCK = next(t for t in _TASKS if t["name"] == "fabric_netplan | Compare the fabric NICs with the fabric file")
filter_loader.add_directory(str(_ROOT / "filter_plugins"))


def role_tasks(action):
    return yaml.safe_load((_ROOT / "roles/fabric_netplan/tasks" / (action + ".yaml")).read_text())


def test_the_block_compares_the_node_output_with_its_fabric_file(tmp_path):
    (tmp_path / "gnr1.yaml").write_text(yaml.safe_dump({"network": {"version": 2, "ethernets": FABRIC}}))
    stdout = json.dumps([LO, MGMT, link("ens6f0", addr("10.201.1.2", 16), addr("192.168.0.5", 24))])
    variables = {name: trust_as_template(v) for name, v in _BLOCK["vars"].items()}
    variables.update(item={"item": "n1", "stdout": stdout}, hostvars={"n1": {"node_hostname": "gnr1"}},
                     rack_scale_netplan_dir=str(tmp_path))
    assert render("{{ _problems }}", variables) == {"extra": ["ens6f0: has 192.168.0.5/24"], "wrong": []}
    assert render("{{ _nic_diff }}", variables) == (
        "On n1 the fabric NICs are not as %s/gnr1.yaml sets them: ens6f0: has 192.168.0.5/24." % tmp_path)


@pytest.mark.parametrize("flag", [False, "false", True, "true"])
def test_the_flag_turns_only_extra_addresses_into_a_warning(flag):
    fail, warn = _BLOCK["block"]
    allowed = str(flag).lower() == "true"

    def runs(task, extra, wrong):
        variables = {"rack_scale_netplan_allow_extra_addresses": flag, "_problems": {"extra": extra, "wrong": wrong}}
        return render("{{ %s }}" % task["when"], variables)
    has, missing = ["ens6f0: has 192.168.0.5/24"], ["ens6f0: 10.201.1.2/16 is missing"]
    assert (runs(fail, has, []), runs(warn, has, [])) == (not allowed, allowed)
    assert (runs(fail, has, missing), runs(warn, has, missing)) == (True, False)
    assert (runs(fail, [], missing), runs(warn, [], missing)) == (True, False)
    assert (runs(fail, [], []), runs(warn, [], [])) == (False, False)


def test_the_stop_offers_the_flag_only_for_extra_addresses():
    fail = _BLOCK["block"][0]

    def msg(wrong):
        return render(fail["ansible.builtin.fail"]["msg"], {"_nic_diff": "On n1 ...", "_problems": {"extra": [], "wrong": wrong}})
    assert msg([]).endswith("Or set rack_scale_netplan_allow_extra_addresses: true to continue with a warning.")
    assert msg(["ens6f0: no such NIC"]).endswith("see max_mtu in ip -d link.")


def test_the_node_address_check_reads_the_same_output():
    task = next(t for t in _TASKS if t["name"] == "fabric_netplan | Fail if fabric address not present")
    stdout = json.dumps([LO, link("ens6f0", addr("10.201.1.2", 16))])

    def fails(access_ip):
        return render("{{ %s }}" % task["when"], {"item": {"item": "n1", "stdout": stdout},
                                                   "hostvars": {"n1": {"access_ip": access_ip}}})
    assert (fails("10.201.1.2"), fails("10.201.1.20"), fails("0.201.1.2")) == (False, True, True)


def test_one_node_list_and_the_topology_none_skip():
    groups = {"kube_control_plane": ["cp1", "localhost"], "kube_node": ["w1", "cp1"], "ceph_storage_nodes": ["s1"]}
    node_list = role_tasks("node_list")[0]["ansible.builtin.set_fact"]["_fabric_all_nodes"]
    assert sorted(render(node_list, {"groups": groups})) == ["cp1", "s1", "w1"]
    nodes = role_tasks("nodes")
    skip = next(t for t in nodes if t.get("ansible.builtin.meta") == "end_role")
    assert render("{{ %s }}" % skip["when"], {"rack_scale_topology": "none"}) is True
    for tasks in (nodes, role_tasks("teardown")):
        assert "node_list.yaml" in [t.get("ansible.builtin.import_tasks") for t in tasks]
    for action in ("install", "validate"):
        imports = [t.get("ansible.builtin.import_tasks") for t in role_tasks(action)]
        assert imports.index("nodes.yaml") < imports.index("check_nics.yaml")
