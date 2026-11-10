# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import importlib.util
import pathlib
import textwrap

_PROBE = pathlib.Path(__file__).resolve().parents[3] / "roles" / "fabric_netplan" / "files" / "netplan_probe.py"
_spec = importlib.util.spec_from_file_location("netplan_probe", _PROBE)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def probe(tmp_path, files, nics=("ens6f0",), macs=None, drivers=None):
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    for nic, mac in (macs or {}).items():
        (tmp_path / "sys/class/net" / nic).mkdir(parents=True, exist_ok=True)
        (tmp_path / "sys/class/net" / nic / "address").write_text(mac + "\n")
    for nic, drv in (drivers or {}).items():
        (tmp_path / "sys/class/net" / nic / "device").mkdir(parents=True, exist_ok=True)
        (tmp_path / "drivers" / drv).mkdir(parents=True, exist_ok=True)
        (tmp_path / "sys/class/net" / nic / "device" / "driver").symlink_to(tmp_path / "drivers" / drv)
    lines = _mod.scan(str(tmp_path), "99-fabric.yaml", "60-fabric.yaml", list(nics))
    return [line.replace(str(tmp_path), "") for line in lines]


def cloud_init(entries):
    return {"etc/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n" + textwrap.indent(entries, "    ")}


MAC = "aa:bb:cc:dd:ee:01"


def test_same_id_dhcp_is_overridden_by_the_later_fabric_file(tmp_path):
    assert probe(tmp_path, cloud_init("ens6f0: {dhcp4: true, dhcp6: true}\n")) == []


def test_same_id_dhcp_in_a_file_after_the_fabric_file_is_reported(tmp_path):
    files = {"etc/netplan/99-zz-local.yaml": "network:\n  ethernets:\n    ens6f0: {dhcp4: true}\n"}
    assert probe(tmp_path, files) == ["netplandef=ens6f0 in /etc/netplan/99-zz-local.yaml ('ens6f0' sets dhcp4)"]


def test_same_id_lists_and_gateway_are_reported(tmp_path):
    entries = "ens6f0: {dhcp4: true, addresses: [192.168.0.5/24], gateway4: 192.168.0.1}\n"
    assert probe(tmp_path, cloud_init(entries)) == [
        "netplandef=ens6f0 in /etc/netplan/50-cloud-init.yaml ('ens6f0' sets addresses, gateway4)"]


def test_a_matching_entry_is_reported_with_its_dhcp(tmp_path):
    entries = "all-en:\n  match: {name: 'en*'}\n  dhcp4: true\n"
    assert probe(tmp_path, cloud_init(entries), ("ens6f0", "ens28f0")) == [
        "netplandef=ens6f0 in /etc/netplan/50-cloud-init.yaml ('all-en' matches it and sets dhcp4)",
        "netplandef=ens28f0 in /etc/netplan/50-cloud-init.yaml ('all-en' matches it and sets dhcp4)"]


def test_a_matching_entry_that_sets_nothing_is_reported(tmp_path):
    assert probe(tmp_path, cloud_init("all-en:\n  match: {name: 'en*'}\n  dhcp4: no\n")) == [
        "netplandef=ens6f0 in /etc/netplan/50-cloud-init.yaml ('all-en' matches it)"]


def test_match_keys_must_all_match(tmp_path):
    entries = "mgmt:\n  match: {name: 'en*', macaddress: 'aa:bb:cc:00:00:99'}\n  dhcp4: true\n"
    assert probe(tmp_path, cloud_init(entries), macs={"ens6f0": MAC}) == []
    entries = "fab:\n  match: {name: 'en*', macaddress: '%s'}\n  dhcp4: true\n" % MAC.upper()
    assert probe(tmp_path, cloud_init(entries), macs={"ens6f0": MAC}) == [
        "netplandef=ens6f0 in /etc/netplan/50-cloud-init.yaml ('fab' matches it and sets dhcp4)"]


def test_match_by_driver(tmp_path):
    files = {"etc/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n"
             "    mgmt: {match: {name: 'en*', driver: igb}, dhcp4: true}\n"
             "    fab: {match: {driver: [ice, mlx5_core]}, dhcp4: true}\n"}
    assert probe(tmp_path, files, drivers={"ens6f0": "ice"}) == [
        "netplandef=ens6f0 in /etc/netplan/50-cloud-init.yaml ('fab' matches it and sets dhcp4)"]


def test_mac_match_set_name_and_bond_member_by_name(tmp_path):
    files = {"run/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n    id0:\n"
                                               "      match: {macaddress: 'AA:bb:cc:dd:ee:01'}\n      set-name: fab0\n",
             "etc/netplan/02-bond.yaml": "network:\n  bonds:\n    bond0:\n      interfaces: [ens28f0]\n"}
    assert sorted(probe(tmp_path, files, ("ens6f0", "ens28f0"), macs={"ens6f0": MAC})) == [
        "netplandef=ens28f0 in /etc/netplan/02-bond.yaml (member of bond 'bond0')",
        "netplandef=ens6f0 in /run/netplan/50-cloud-init.yaml ('id0' matches it and sets set-name)"]


def test_bond_and_bridge_members_are_netplan_ids(tmp_path):
    files = {"etc/netplan/01-eth.yaml": "network:\n  ethernets:\n    id0: {match: {macaddress: '%s'}}\n"
                                        "    id1: {match: {name: 'ens28*'}}\n" % MAC,
             "etc/netplan/02-bond.yaml": "network:\n  bonds:\n    bond0: {interfaces: [id0]}\n"
                                         "  bridges:\n    br0: {interfaces: [id1]}\n"}
    assert sorted(probe(tmp_path, files, ("ens6f0", "ens28f0"), macs={"ens6f0": MAC})) == [
        "netplandef=ens28f0 in /etc/netplan/01-eth.yaml ('id1' matches it)",
        "netplandef=ens28f0 in /etc/netplan/02-bond.yaml (member of bridge 'br0')",
        "netplandef=ens6f0 in /etc/netplan/01-eth.yaml ('id0' matches it)",
        "netplandef=ens6f0 in /etc/netplan/02-bond.yaml (member of bond 'bond0')"]


def test_networkmanager_profiles(tmp_path):
    files = {"etc/netplan/90-NM-x.yaml": "network:\n  ethernets:\n    NM-x:\n      renderer: NetworkManager\n"
                                         "      match: {name: ens6f0}\n      networkmanager: {name: 'Wired 1'}\n",
             "etc/NetworkManager/system-connections/w.nmconnection": "[connection]\nid=w\ninterface-name=ens28f0\n",
             "etc/NetworkManager/system-connections/other.nmconnection": "[connection]\nid=o\ninterface-name=ens28f0\n"
                                                                        "[ethernet]\nmac-address=00:00:00:00:00:01\n"}
    assert probe(tmp_path, files, ("ens6f0", "ens28f0"), macs={"ens28f0": "aa:bb:cc:dd:ee:02"}) == [
        "nmprof=ens6f0 in /etc/netplan/90-NM-x.yaml (profile 'Wired 1')",
        "nmprof=ens28f0 in /etc/NetworkManager/system-connections/w.nmconnection (profile 'w')"]


def test_lib_netplan_and_shadowing(tmp_path):
    files = {"lib/netplan/10-lib.yaml": "network:\n  ethernets:\n    ens6f0: {addresses: [192.168.0.5/24]}\n",
             "etc/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n    ens6f0: {addresses: [192.168.0.6/24]}\n",
             "run/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n    eth0: {dhcp4: true}\n"}
    assert probe(tmp_path, files) == ["netplandef=ens6f0 in /lib/netplan/10-lib.yaml ('ens6f0' sets addresses)"]


def test_benign_unparseable_and_the_fabric_files(tmp_path):
    files = {"etc/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n    ens6f0: {dhcp4: no, optional: true}\n"
                                               "    eth0: {addresses: [192.168.0.5/24]}\n",
             "etc/netplan/bad.yaml": "network: [unclosed\n",
             "etc/netplan/odd.yaml": "network: hello\n",
             "etc/netplan/60-fabric.yaml": "network:\n  ethernets:\n    ens6f0: {addresses: [10.0.0.1/16]}\n",
             "etc/netplan/99-fabric.yaml": "network:\n  ethernets:\n    ens6f0: {addresses: [10.0.0.1/16]}\n"}
    assert probe(tmp_path, files) == []


def test_a_file_that_is_not_utf8_is_skipped(tmp_path):
    (tmp_path / "etc/netplan").mkdir(parents=True)
    (tmp_path / "etc/netplan/50-latin1.yaml").write_bytes(b"network:\n  ethernets:\n    ens6f0: {addresses: [10.9.9.9/24]} # \xe9\n")
    assert probe(tmp_path, {}) == []


def test_an_id_matched_in_one_file_carries_the_keys_another_file_sets(tmp_path):
    files = {"etc/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n    id0:\n      match: {macaddress: '%s'}\n" % MAC,
             "etc/netplan/70-local.yaml": "network:\n  ethernets:\n    id0: {addresses: [192.168.0.7/24]}\n"
                                          "  bonds:\n    bond0: {interfaces: [id0]}\n"}
    assert probe(tmp_path, files, macs={"ens6f0": MAC}) == [
        "netplandef=ens6f0 in /etc/netplan/50-cloud-init.yaml ('id0' matches it)",
        "netplandef=ens6f0 in /etc/netplan/70-local.yaml ('id0' sets addresses)",
        "netplandef=ens6f0 in /etc/netplan/70-local.yaml (member of bond 'bond0')"]


def test_cloud_init_entry_named_after_the_fabric_nic_with_its_own_mac_is_harmless(tmp_path):
    entries = "ens6f0:\n  match: {macaddress: '%s'}\n  set-name: ens6f0\n  dhcp4: true\n" % MAC
    assert probe(tmp_path, cloud_init(entries), macs={"ens6f0": MAC}) == []


def test_entry_named_after_the_fabric_nic_still_reports_what_it_adds(tmp_path):
    entries = "ens6f0:\n  match: {macaddress: '%s'}\n  set-name: ens6f0\n  addresses: [192.168.0.7/24]\n" % MAC
    assert probe(tmp_path, cloud_init(entries), macs={"ens6f0": MAC}) == [
        "netplandef=ens6f0 in /etc/netplan/50-cloud-init.yaml ('ens6f0' sets addresses)"]


def test_entry_named_after_the_fabric_nic_that_matches_another_nic_is_reported(tmp_path):
    entries = "ens6f0:\n  match: {macaddress: 'aa:bb:cc:00:00:99'}\n  set-name: ens6f0\n"
    assert probe(tmp_path, cloud_init(entries), macs={"ens6f0": MAC}) == [
        "netplandef=ens6f0 in /etc/netplan/50-cloud-init.yaml ('ens6f0' has a match: that selects a different NIC)"]
