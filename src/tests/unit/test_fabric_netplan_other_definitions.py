# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
# Runs the netplan probe embedded in roles/fabric_netplan/tasks/install.yaml against fixture files.
import pathlib
import subprocess
import sys
import textwrap

import yaml

_TASKS = pathlib.Path(__file__).resolve().parents[3] / "roles" / "fabric_netplan" / "tasks" / "install.yaml"
_SHELL = next(t for t in yaml.safe_load(_TASKS.read_text())
              if t.get("name", "").startswith("fabric_netplan | Probe"))["ansible.builtin.shell"]
_PROBE = textwrap.dedent(_SHELL.split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0]).replace(
    "{{ rack_scale_netplan_dest | basename }}", "99-fabric.yaml")


def probe(tmp_path, files, nics=("ens6f0",)):
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    code = _PROBE
    for prefix in ("/lib/netplan/", "/etc/netplan/", "/run/netplan/", "/etc/NetworkManager/", "/sys/class/net/"):
        code = code.replace("'" + prefix, "'%s%s" % (tmp_path, prefix))
    out = subprocess.run([sys.executable, "-I", "-", *nics], input=code, capture_output=True, text=True, check=True)
    return [line.replace(str(tmp_path), "") for line in out.stdout.splitlines()]


def cloud_init(entries):
    return {"etc/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n" + textwrap.indent(entries, "    ")}


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


def test_mac_match_and_bond_member(tmp_path):
    files = {"run/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n    id0:\n"
                                               "      match: {macaddress: 'AA:bb:cc:dd:ee:01'}\n      dhcp4: true\n",
             "etc/netplan/02-bond.yaml": "network:\n  bonds:\n    bond0:\n      interfaces: [ens28f0]\n",
             "sys/class/net/ens6f0/address": "aa:bb:cc:dd:ee:01\n"}
    assert sorted(probe(tmp_path, files, ("ens6f0", "ens28f0"))) == [
        "netplandef=ens28f0 in /etc/netplan/02-bond.yaml (member of bond 'bond0')",
        "netplandef=ens6f0 in /run/netplan/50-cloud-init.yaml ('id0' matches it and sets dhcp4)"]


def test_benign_entries_and_the_fabric_file_itself(tmp_path):
    files = {"etc/netplan/50-cloud-init.yaml": "network:\n  ethernets:\n    ens6f0: {dhcp4: no, optional: true}\n"
                                               "    eth0: {addresses: [192.168.0.5/24]}\n",
             "etc/netplan/99-fabric.yaml": "network:\n  ethernets:\n    ens6f0: {addresses: [10.0.0.1/16]}\n"}
    assert probe(tmp_path, files) == []
