# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import pathlib

import pytest
import yaml
from ansible_render import render

_TEMPLATES = pathlib.Path(__file__).resolve().parents[3] / "roles" / "kubernetes" / "templates"
_RESOLVED = "/run/systemd/resolve/resolv.conf"
_ETC = "/etc/resolv.conf"


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


_EXTRA_AUTO_VARS = {
    "env_dir": "/env", "playbook_dir": "/playbooks", "kubernetes_version": "v1", "kubernetes_cni": "calico",
    "kubernetes_container_manager": "containerd", "kubernetes_cluster_cidr": "pods", "kubernetes_service_cidr": "services",
    "kubernetes_dns_mode": "coredns", "kubernetes_kube_proxy_mode": "iptables", "_k8s_needs_ssh": False,
    "kubespray_bin_dir_local": "/kubespray/bin", "kubernetes_accelerator": "cpu", "kubernetes_apiserver_authn_enabled": False,
}


def extra_auto(**overrides):
    text = render((_TEMPLATES / "kubespray_extra_auto.yml.j2").read_text(), {**_EXTRA_AUTO_VARS, **overrides})
    return yaml.safe_load(text)


@pytest.mark.parametrize("backend, is_default", [("local-path", "true"), ("nfs", "false")])
def test_local_path_class(backend, is_default):
    values = extra_auto(_resolved_storage_backend=backend)
    assert values["local_path_provisioner_enabled"] is True
    assert values["local_path_provisioner_is_default_storageclass"] == is_default


def test_no_local_path_class_on_other_backends():
    assert "local_path_provisioner_enabled" not in extra_auto(_resolved_storage_backend="ceph")


def test_inotify_limits_on_every_node():
    sysctl = {item["name"]: item["value"] for item in extra_auto()["additional_sysctl"]}
    assert sysctl == {"fs.inotify.max_user_instances": 8192, "fs.inotify.max_user_watches": 1048576}
