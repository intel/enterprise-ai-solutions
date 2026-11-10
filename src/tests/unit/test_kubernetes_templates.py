# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import pathlib

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template

_TEMPLATES = pathlib.Path(__file__).resolve().parents[3] / "roles" / "kubernetes" / "templates"
_RESOLVED = "/run/systemd/resolve/resolv.conf"
_ETC = "/etc/resolv.conf"


def render(source, variables):
    return Templar(loader=DataLoader(), variables=variables).template(trust_as_template(source))


def inventory(control_plane, workers):
    text = render((_TEMPLATES / "hosts.yaml.j2").read_text(), {
        "_nodes_control_plane": control_plane,
        "_nodes_workers": workers,
        "_nodes_ssh_user": "deploy",
        "_nodes_ssh_key": "/keys/id",
    })
    return yaml.safe_load(text)["all"]


def kube_resolv_conf(facts):
    template = (_TEMPLATES / "kubespray_extra_auto.yml.j2").read_text()
    line = next(l for l in template.splitlines() if l.startswith("kube_resolv_conf:"))
    expression = yaml.safe_load(render(line, {}))["kube_resolv_conf"]
    return render(expression, {"ansible_facts": facts})


def test_remote_nodes_connect_over_ssh():
    inv = inventory([{"ip": "10.0.0.1", "hostname": "cp1"}],
                    [{"ip": "10.0.0.2", "hostname": "w1", "ssh_user": "ops", "ssh_key": "/keys/w1"}])
    assert inv["hosts"] == {
        "cp1": {"ansible_host": "10.0.0.1", "ansible_user": "deploy", "ansible_ssh_private_key_file": "/keys/id"},
        "w1": {"ansible_host": "10.0.0.2", "ansible_user": "ops", "ansible_ssh_private_key_file": "/keys/w1"},
    }
    assert inv["children"]["k8s_cluster"]["vars"]["ansible_connection"] == "ssh"


@pytest.mark.parametrize("node", [
    {"ip": "10.0.0.1", "hostname": "localhost"},
    {"ip": "127.0.0.1", "hostname": "node1"},
    {"ip": "::1", "hostname": "node1"},
])
def test_installer_node_stays_local_and_keeps_its_address(node):
    inv = inventory([node], [{"ip": "10.0.0.2", "hostname": "w1"}])
    assert inv["hosts"][node["hostname"]] == {
        "ansible_host": node["ip"],
        "ansible_connection": "local",
        "ansible_python_interpreter": "{{ ansible_playbook_python }}",
    }
    assert "ansible_connection" not in inv["hosts"]["w1"]


@pytest.mark.parametrize(("nameservers", "expected"), [
    (["127.0.0.53"], _RESOLVED),
    (["127.0.0.53", "10.0.0.2"], _RESOLVED),
    (["10.248.2.1"], _ETC),
    (["169.254.25.10", "10.248.2.1"], _ETC),
    (["127.0.0.1"], _ETC),
    ([], _ETC),
])
def test_kubelet_uses_resolved_file_only_behind_its_stub(nameservers, expected):
    assert kube_resolv_conf({"dns": {"nameservers": nameservers}}) == expected


def test_kubelet_resolv_conf_without_dns_facts():
    assert kube_resolv_conf({}) == _ETC
