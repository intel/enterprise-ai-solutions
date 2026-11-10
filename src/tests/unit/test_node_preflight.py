# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import base64
import copy
import importlib.util
import json
import pathlib

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

try:
    from ansible.template import trust_as_template
except ImportError:  # ansible-core < 2.19 templates plain strings
    def trust_as_template(source):
        return source

_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, _ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rules = _load("node_preflight", "filter_plugins/node_preflight.py")
probe = _load("node_preflight_probe", "roles/kubernetes/files/node_preflight_probe.py")

GIB = 1 << 30
CTX = {"skip": [], "local": False, "rack_scale": False, "ceph": False, "proxy_host": "",
       "api_lb": "lb-apiserver.kubernetes.local",
       "api_hosts": ["localhost", "127.0.0.1", "cp1", "192.0.2.10"], "min_free_gib": 20, "warn_free_gib": 50,
       "max_used_pct": 90, "etcd_warn_free_gib": 10, "max_skew_s": 60, "warn_skew_s": 5}
CLEAN_DISK = {"exists": True, "type": "disk", "fstype": None, "children": [], "mountpoints": [],
              "bluestore_labels_gib": []}


def healthy():
    return {
        "node_epoch": 1000.0, "controller_epoch": 999.6, "etcd_node": False, "euid": 0,
        "os": {"id": "ubuntu", "version_id": "24.04", "pretty": "Ubuntu 24.04 LTS"},
        "time": {"source": "chrony", "synced": True, "detail": "Leap status: Normal"},
        "disk": {"root": {"path": "/", "avail": 200 * GIB, "used_pct": 30.0},
                 "containerd": {"path": "/var/lib", "avail": 200 * GIB, "used_pct": 30.0}, "etcd": None},
        "gateway": {"ip": "192.0.2.1", "dev": "eth0", "reachable": True, "via": "icmp"},
        "dns": {"registry.k8s.io": {"ok": True}, "quay.io": {"ok": True}},
        "urls": {"https://github.com": {"ok": True}, "https://registry.k8s.io/v2/": {"ok": True}},
        "kube": {"kubelet_active": False, "kubelet_conf_server": None, "admin_conf_server": None, "manifests": [],
                 "etcd_member": False, "etcd_binary": False, "etcd_unit": False, "cni_calico": [],
                 "addresses": ["127.0.0.1", "192.0.2.30"], "kubelet_conf_ca": None, "admin_conf_ca": None,
                 "cluster_ca": None},
        "ports": [],
        "pkg": {"dpkg_audit": "", "busy": []},
        "firewall": {"ufw": False, "firewalld": False},
        "errors": [],
    }


def evaluate(data, **ctx):
    return rules.node_preflight_evaluate([{"item": "n1", "stdout": json.dumps(data)}], {**CTX, **ctx})


def status(data, cid, **ctx):
    found = [r["status"] for r in evaluate(data, **ctx)["rows"] if r["id"] == cid]
    return found[0] if len(found) == 1 else found


def changed(fn):
    data = copy.deepcopy(healthy())
    fn(data)
    return data


def test_healthy_node_has_no_failures_or_warnings():
    out = evaluate(healthy())
    assert out["failures"] == [] and out["warnings"] == []
    assert out["table"] == ["n1: 13 ok, 0 warn, 0 fail"]


CASES = [
    # name, change to the healthy probe, context, check, expected status
    ("N1 chrony not synced", lambda d: d.update(time={"source": "chrony", "synced": False, "detail": "x"}), {}, "N1", "FAIL"),
    ("N1 no time service", lambda d: d.update(time={"source": "none", "synced": False, "detail": "x"}), {}, "N1", "FAIL"),
    ("N2 3 s", lambda d: d.update(controller_epoch=997.0), {}, "N2", "OK"),
    ("N2 12 s", lambda d: d.update(controller_epoch=988.0), {}, "N2", "WARN"),
    ("N2 -90 s", lambda d: d.update(controller_epoch=1090.0), {}, "N2", "FAIL"),
    ("N2 on the deploy node", lambda d: d.update(controller_epoch=1900.0), {"local": True}, "N2", "OK"),
    ("N2 no deploy-node time", lambda d: d.update(controller_epoch=None), {}, "N2", "WARN"),
    ("N3 15 GiB free", lambda d: d["disk"]["containerd"].update(avail=15 * GIB), {}, "N3", "FAIL"),
    ("N3 35 GiB free", lambda d: d["disk"]["containerd"].update(avail=35 * GIB), {}, "N3", "WARN"),
    ("N3 95% used", lambda d: d["disk"]["containerd"].update(used_pct=95.0), {}, "N3", "FAIL"),
    ("N4 root 92% used", lambda d: d["disk"]["root"].update(used_pct=92.0), {}, "N4", "FAIL"),
    ("N5 etcd 5 GiB free", lambda d: d["disk"].update(etcd={"path": "/var/lib", "avail": 5 * GIB, "used_pct": 50.0}),
     {}, "N5", "WARN"),
    ("N5 no etcd on this node", lambda d: None, {}, "N5", []),
    ("N6 no default route", lambda d: d.update(gateway={"ip": None, "dev": None, "reachable": False, "via": None}),
     {}, "N6", "FAIL"),
    ("N6 gateway unreachable", lambda d: d["gateway"].update(reachable=False), {}, "N6", "FAIL"),
    ("N6 default route without a gateway", lambda d: d.update(gateway={"ip": None, "dev": "wg0", "reachable": False,
                                                                        "via": None}), {}, "N6", "OK"),
    ("N7 name fails, no proxy", lambda d: d["dns"]["quay.io"].update(ok=False), {}, "N7", "FAIL"),
    ("N7 name fails behind a proxy", lambda d: d["dns"]["quay.io"].update(ok=False),
     {"proxy_host": "proxy.example.com"}, "N7", "WARN"),
    ("N7 name fails, internet check skipped", lambda d: d["dns"]["quay.io"].update(ok=False), {"skip": ["N8"]},
     "N7", "WARN"),
    ("N7 proxy host fails", lambda d: d["dns"].update({"proxy.example.com": {"ok": False}}),
     {"proxy_host": "proxy.example.com"}, "N7", "FAIL"),
    ("N8 timeout", lambda d: d["urls"]["https://github.com"].update(ok=False, error="timed out"), {}, "N8", "FAIL"),
    ("N8 proxy 407", lambda d: d["urls"]["https://github.com"].update(ok=False, error="proxy authentication required"),
     {}, "N8", "FAIL"),
    ("N8 403", lambda d: d["urls"]["https://github.com"].update(ok=True, refused=True), {}, "N8", "WARN"),
    ("N8 403 and a timeout", lambda d: (d["urls"]["https://github.com"].update(ok=True, refused=True),
                                        d["urls"]["https://registry.k8s.io/v2/"].update(ok=False, error="timed out")),
     {}, "N8", "FAIL"),
    ("N8 skipped", lambda d: d["urls"]["https://github.com"].update(ok=False), {"skip": ["N8"]}, "N8", "SKIP"),
    ("N8 skipped, so not probed", lambda d: d.pop("urls"), {"skip": ["N8"]}, "N8", "SKIP"),
    ("N9 not root after become", lambda d: d.update(euid=1000), {}, "N9", "FAIL"),
    ("N10 kubelet.conf of another cluster", lambda d: d["kube"].update(kubelet_conf_server="https://198.51.100.9:6443"),
     {}, "N10", "FAIL"),
    ("N10 our control plane", lambda d: d["kube"].update(kubelet_conf_server="https://192.0.2.10:6443"),
     {}, "N10", "OK"),
    ("N10 local server, CA unreadable", lambda d: d["kube"].update(kubelet_conf_server="https://127.0.0.1:6443"),
     {}, "N10", "FAIL"),
    ("N10 local proxy, no control-plane CA in this run",
     lambda d: d["kube"].update(kubelet_conf_server="https://localhost:6443", kubelet_conf_ca="ca-a"), {}, "N10", "FAIL"),
    ("N11 our leftovers", lambda d: d["kube"].update(kubelet_active=True, etcd_member=True), {}, "N11", "WARN"),
    ("N11 kubelet on its port", lambda d: d.update(ports=[{"port": 10250, "proc": "kubelet", "pid": 1}]),
     {}, "N11", "WARN"),
    ("N12 kubelet on its port is ours", lambda d: d.update(ports=[{"port": 10250, "proc": "kubelet", "pid": 1}]),
     {}, "N12", "OK"),
    ("N12 another program on 6443", lambda d: d.update(ports=[{"port": 6443, "proc": "nc", "pid": 42}]),
     {}, "N12", "FAIL"),
    ("N13 dpkg broken", lambda d: d["pkg"].update(dpkg_audit="The following packages are only half configured"),
     {}, "N13", "FAIL"),
    ("N13 apt busy", lambda d: d["pkg"].update(busy=["unattended-upgr"]), {}, "N13", "WARN"),
    ("N13 rpm system", lambda d: d["pkg"].update(dpkg_audit=None), {}, "N13", "OK"),
    ("N14 ufw active", lambda d: d["firewall"].update(ufw=True), {}, "N14", "WARN"),
    ("N15 not rack-scale", lambda d: d["os"].update(id="rocky", version_id="9.4"), {}, "N15", []),
    ("N15 rack-scale on Rocky", lambda d: d["os"].update(id="rocky", version_id="9.4"), {"rack_scale": True},
     "N15", "FAIL"),
    ("N15 rack-scale on 24.04", lambda d: None, {"rack_scale": True}, "N15", "OK"),
    ("N16 not Ceph", lambda d: d.update(ceph={"/dev/sdb": {"exists": False}}), {}, "N16", []),
    ("N16 clean disk", lambda d: d.update(ceph={"/dev/nvme1n1": CLEAN_DISK}), {"ceph": True}, "N16", "OK"),
    ("N16 missing disk", lambda d: d.update(ceph={"/dev/nvme9n9": {"exists": False}}), {"ceph": True}, "N16", "FAIL"),
    ("N16 OS disk", lambda d: d.update(ceph={"/dev/sda": {**CLEAN_DISK, "children": ["sda1"], "mountpoints": ["/"]}}),
     {"ceph": True}, "N16", "FAIL"),
    ("N16 old BlueStore label", lambda d: d.update(ceph={"/dev/nvme1n1": {**CLEAN_DISK, "bluestore_labels_gib": [10]}}),
     {"ceph": True}, "N16", "FAIL"),
    ("N16 xfs signature", lambda d: d.update(ceph={"/dev/nvme1n1": {**CLEAN_DISK, "fstype": "xfs"}}),
     {"ceph": True}, "N16", "FAIL"),
    ("N19 not an etcd node", lambda d: d["kube"].update(etcd_binary="/usr/local/bin/etcd"), {}, "N19", []),
    ("N19 binary without a service", lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary="/usr/local/bin/etcd")),
     {}, "N19", "FAIL"),
    ("N19 installed etcd", lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary="/usr/local/bin/etcd", etcd_unit=True)),
     {}, "N19", "OK"),
    ("N11 installed etcd is a leftover",
     lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary="/usr/local/bin/etcd", etcd_unit=True)), {}, "N11", "WARN"),
    ("N19 clean etcd node", lambda d: d.update(etcd_node=True), {}, "N19", "OK"),
    ("N0 probe error", lambda d: d.update(errors=["dns: boom"]), {}, "N0", "WARN"),
]


@pytest.mark.parametrize("change, ctx, cid, expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_check(change, ctx, cid, expected):
    assert status(changed(change), cid, **ctx) == expected


def test_sudo_warnings_before_the_probe_json_are_ignored():
    stdout = "sudo: unable to resolve host n1: Name or service not known\r\n" + json.dumps(healthy()) + "\r\n"
    out = rules.node_preflight_evaluate([{"item": "n1", "stdout": stdout}], CTX)
    assert out["failures"] == [] and out["warnings"] == []


@pytest.mark.parametrize("result, cid, evidence", [
    ({"failed": True, "msg": "Missing sudo password"}, "N9", "Missing sudo password"),
    ({"failed": True, "msg": "/bin/sh: python3: not found", "rc": 127}, "N0", "/bin/sh: python3: not found"),
    # The script ran and failed: its output says why, Ansible's msg does not.
    ({"failed": True, "msg": "non-zero return code", "rc": 127, "stdout": "/bin/sh: 1: python3: not found\r\n",
      "stderr": "Shared connection to n1 closed.\r\n"}, "N0", "/bin/sh: 1: python3: not found"),
    ({"failed": True, "msg": "non-zero return code", "rc": 1, "stdout": "",
      "stderr": "sudo: a password is required"}, "N9", "sudo: a password is required"),
    # sudo's host-name warning is not a sudo failure.
    ({"failed": True, "msg": "non-zero return code", "rc": 127, "stdout": "sudo: unable to resolve host n1: Name or "
      "service not known\r\n/bin/sh: 1: python3: not found\r\n"}, "N0", "/bin/sh: 1: python3: not found"),
    ({"failed": True, "msg": "non-zero return code", "rc": 1, "stdout": "sudo: unable to resolve host n1\r\n"
      "sudo: a terminal is required to read the password\r\n"}, "N9", "sudo: a terminal is required to read the password"),
    # Unreachable: SSH, or a become timeout (ignore_unreachable hands these to the rules).
    ({"unreachable": True, "msg": "Task failed: Failed to connect to the host via ssh: ssh: connect to host 192.0.2.9 "
      "port 22: Connection timed out"}, "N0", "Task failed: Failed to connect to the host via ssh: ssh: connect to "
      "host 192.0.2.9 port 22: Connection timed out"),
    ({"unreachable": True, "msg": "Task failed: Timeout (12s) waiting for privilege escalation prompt: "}, "N9",
     "Task failed: Timeout (12s) waiting for privilege escalation prompt:"),
])
def test_probe_that_could_not_run(result, cid, evidence):
    out = rules.node_preflight_evaluate([{"item": "n1", "stdout": "", **result}], CTX)
    assert [(r["id"], r["status"], r["evidence"]) for r in out["rows"]] == [(cid, "FAIL", evidence)]


@pytest.mark.parametrize("value, expected", [
    (["N8"], ["N8"]), (["n8", " N7 "], ["N7", "N8"]), ("n8", ["N8"]), ("N7,n8", ["N7", "N8"]),
    ("", []), (None, []), ([], []),
])
def test_skip_list_is_normalised(value, expected):
    assert rules.node_preflight_skip(value) == expected


@pytest.mark.parametrize("value", [["N08"], "N8,N99", ["N0"]])
def test_skip_list_rejects_unknown_checks(value):
    with pytest.raises(ValueError, match="no check"):
        rules.node_preflight_skip(value)


def test_run_uses_the_c_locale(monkeypatch):
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    assert probe.run(["sh", "-c", 'printf %s "$LC_ALL"']) == (0, "C")


def test_run_can_leave_stderr_out():
    assert probe.run(["sh", "-c", "echo warning >&2; echo listed"], stderr=False) == (0, "listed")
    assert probe.run(["sh", "-c", "echo warning >&2"]) == (0, "warning")


@pytest.mark.parametrize("answer, expected", [((0, ""), "OK"), ((1, ""), "FAIL"), ((0, "pkg half-configured"), "FAIL")])
def test_dpkg_audit_is_judged_on_its_stdout_and_exit_status(monkeypatch, answer, expected):
    monkeypatch.setattr(probe, "run", _fake_run({"dpkg": answer}))
    pkg = probe.package_manager()
    assert status(changed(lambda d: d.update(pkg=pkg)), "N13") == expected


def test_run_tells_a_timeout_from_a_missing_command():
    assert probe.run(["no-such-command-np"]) == (None, "")
    rc, out = probe.run(["sleep", "5"], timeout=0.2)
    assert rc == -1 and "timed out" in out


def test_a_dpkg_audit_that_times_out_is_not_ok(monkeypatch):
    monkeypatch.setattr(probe, "run", _fake_run({"dpkg": (-1, "Command 'dpkg --audit' timed out after 30 seconds")}))
    pkg = probe.package_manager()
    assert status(changed(lambda d: d.update(pkg=pkg)), "N13") == "FAIL"


def test_package_manager_reports_a_busy_apt(monkeypatch):
    comms = {"/proc/10/comm": "unattended-upgr", "/proc/11/comm": "bash"}
    monkeypatch.setattr(probe.os, "listdir", lambda path: ["10", "11", "self"])
    monkeypatch.setattr(probe, "read", lambda path, default="": comms.get(path, default))
    monkeypatch.setattr(probe, "run", _fake_run({"dpkg": (0, "")}))
    assert probe.package_manager() == {"dpkg_audit": "", "busy": ["unattended-upgr"]}


@pytest.mark.parametrize("answers, expected", [
    ({"ufw": (0, "Status: active\n\nTo  Action  From"), "systemctl": (3, "inactive")}, {"ufw": True, "firewalld": False}),
    ({"ufw": (0, "Status: inactive"), "systemctl": (0, "active")}, {"ufw": False, "firewalld": True}),
    ({"systemctl": (3, "inactive")}, {"ufw": None, "firewalld": False}),
])
def test_firewall(monkeypatch, answers, expected):
    monkeypatch.setattr(probe, "run", _fake_run(answers))
    assert probe.firewall() == expected


def test_resolve_bounds_each_name(monkeypatch):
    def getaddrinfo(name, port):
        if name == "slow.example.com":
            probe.time.sleep(2)
        if name == "bad.example.com":
            raise probe.socket.gaierror("Name or service not known")
    monkeypatch.setattr(probe.socket, "getaddrinfo", getaddrinfo)
    assert probe.resolve(["ok.example.com", "bad.example.com", "slow.example.com"], timeout=0.2) == {
        "ok.example.com": {"ok": True}, "bad.example.com": {"ok": False, "error": "Name or service not known"},
        "slow.example.com": {"ok": False, "error": "timed out after 0.2s"}}


def test_disk_uses_the_nearest_existing_parent(tmp_path):
    out = probe.disk(str(tmp_path / "not" / "there"))
    assert out["path"] == str(tmp_path) and out["avail"] > 0 and 0 <= out["used_pct"] <= 100


def _fake_run(answers):
    return lambda args, timeout=10, stderr=True: answers.get(args[0], (None, ""))


def test_time_sync_falls_back_when_chronyd_is_stopped(monkeypatch):
    monkeypatch.setattr(probe, "run", _fake_run({"chronyc": (1, "506 Cannot talk to daemon"),
                                                  "timedatectl": (0, "yes")}))
    assert probe.time_sync() == {"source": "timedatectl", "synced": True, "detail": "NTPSynchronized=yes"}


def test_time_sync_uses_chrony_when_it_answers(monkeypatch):
    monkeypatch.setattr(probe, "run", _fake_run({"chronyc": (0, "Leap status     : Not synchronised"),
                                                  "timedatectl": (0, "yes")}))
    assert probe.time_sync()["synced"] is False


def test_time_sync_without_timedatectl_reports_chrony(monkeypatch):
    monkeypatch.setattr(probe, "run", _fake_run({"chronyc": (1, "506 Cannot talk to daemon")}))
    assert probe.time_sync() == {"source": "chrony", "synced": False, "detail": "506 Cannot talk to daemon"}


def n10(results, api_hosts, control_plane):
    out = rules.node_preflight_evaluate(
        [{"item": node, "stdout": json.dumps(data)} for node, data in results],
        {**CTX, "api_hosts": api_hosts, "control_plane": control_plane})
    return {r["node"]: r["status"] for r in out["rows"] if r["id"] == "N10"}


DEFAULT_HOSTS = ["localhost", "127.0.0.1", "::1", "lb-apiserver.kubernetes.local"]


def test_n10_single_node_rerun_keeps_its_own_cluster():
    # No ip in the inventory: Kubespray binds the API server to the default-route address.
    data = changed(lambda d: d["kube"].update(admin_conf_server="https://192.0.2.77:6443",
                                               addresses=["127.0.0.1", "192.0.2.77", "::1"]))
    assert n10([("localhost", data)], DEFAULT_HOSTS + ["localhost"], ["localhost"]) == {"localhost": "OK"}


def test_n10_dns_name_inventory_counts_the_control_plane_addresses():
    cp1 = changed(lambda d: d["kube"].update(admin_conf_server="https://192.0.2.10:6443", addresses=["192.0.2.10"]))
    w1 = changed(lambda d: d["kube"].update(kubelet_conf_server="https://192.0.2.10:6443", addresses=["192.0.2.20"]))
    w2 = changed(lambda d: d["kube"].update(kubelet_conf_server="https://198.51.100.9:6443",
                                             addresses=["198.51.100.9"]))
    hosts = DEFAULT_HOSTS + ["cp1", "cp1.example.com"]
    assert n10([("cp1", cp1), ("w1", w1), ("w2", w2)], hosts, ["cp1"]) == {"cp1": "OK", "w1": "OK", "w2": "FAIL"}


def test_n10_ipv6_api_server_address():
    cp1 = changed(lambda d: d["kube"].update(admin_conf_server="https://[fd00::10]:6443", addresses=["fd00:0::10"]))
    assert n10([("cp1", cp1)], DEFAULT_HOSTS + ["cp1"], ["cp1"]) == {"cp1": "OK"}


def test_n10_loopback_server_is_ours_only_with_our_ca():
    cp1 = changed(lambda d: d["kube"].update(kubelet_conf_server="https://127.0.0.1:6443", kubelet_conf_ca="ca-a",
                                               admin_conf_server="https://127.0.0.1:6443", admin_conf_ca="ca-a",
                                               cluster_ca="ca-a", addresses=["192.0.2.10"]))
    w1 = changed(lambda d: d["kube"].update(kubelet_conf_server="https://localhost:6443", kubelet_conf_ca="ca-a"))
    w2 = changed(lambda d: d["kube"].update(kubelet_conf_server="https://localhost:6443", kubelet_conf_ca="ca-b"))
    assert n10([("cp1", cp1), ("w1", w1), ("w2", w2)], DEFAULT_HOSTS + ["cp1"], ["cp1"]) == {
        "cp1": "OK", "w1": "OK", "w2": "FAIL"}


def n10_rows(results, control_plane=("cp1", "cp2")):
    out = rules.node_preflight_evaluate(results, {**CTX, "api_hosts": list(control_plane),
                                                  "control_plane": list(control_plane)})
    return {r["node"]: (r["status"], r["evidence"]) for r in out["rows"] if r["id"] in ("N10", "N0", "N9")}


def probed(node, **kube):
    return {"item": node, "stdout": json.dumps(changed(lambda d: d["kube"].update(**kube)))}


def test_n10_control_plane_nodes_with_different_cas():
    rows = n10_rows([probed("cp1", cluster_ca="ca-a"), probed("cp2", cluster_ca="ca-b"),
                     probed("w1", kubelet_conf_server="https://localhost:6443", kubelet_conf_ca="ca-a")])
    assert rows["cp1"] == rows["cp2"] == ("FAIL", "this run's control-plane nodes have 2 different cluster CAs")


def test_n10_cannot_compare_while_a_control_plane_node_is_not_probed():
    rows = n10_rows([probed("cp1", cluster_ca="ca-a"), {"item": "cp2", "stdout": "", "msg": "Missing sudo password"},
                     probed("w1", kubelet_conf_server="https://localhost:6443", kubelet_conf_ca="ca-b")])
    assert rows["w1"] == ("WARN", "cannot compare the CA of kubelet.conf: cp2 not probed")
    assert rows["cp2"][0] == "FAIL"


def test_n10_loopback_kubeconfig_without_a_readable_ca():
    rows = n10_rows([probed("cp1", cluster_ca="ca-a"), probed("w1", kubelet_conf_server="https://localhost:6443")],
                    ("cp1",))
    assert rows["w1"] == ("FAIL", "kubelet.conf points at https://localhost:6443, left over from a cluster; "
                                  "its CA is missing or unreadable")


@pytest.mark.parametrize("ca, expected", [("ca-a", "OK"), ("ca-b", "FAIL")])
def test_n10_load_balancer_name_is_decided_by_ca(ca, expected):
    # Kubespray's apiserver_loadbalancer_domain_name is the same in every cluster, so naming it proves nothing.
    results = [probed("cp1", cluster_ca="ca-a"),
               probed("w1", kubelet_conf_server="https://lb-apiserver.kubernetes.local:6443", kubelet_conf_ca=ca)]
    out = rules.node_preflight_evaluate(results, {**CTX, "api_hosts": ["cp1", "lb-apiserver.kubernetes.local"],
                                                  "control_plane": ["cp1"]})
    assert [r["status"] for r in out["rows"] if r["id"] == "N10" and r["node"] == "w1"] == [expected]


def test_etcd_in_the_deploy_nodes_kubespray_bin_dir(tmp_path, monkeypatch):
    (tmp_path / "etcd").write_text("")
    monkeypatch.setattr(probe, "run", _fake_run({}))
    kube = probe.kube_leftovers(["/usr/local/bin", str(tmp_path)])
    assert kube["etcd_binary"] == str(tmp_path / "etcd")
    rows = evaluate(changed(lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary=kube["etcd_binary"]))))
    assert [r["evidence"] for r in rows["rows"] if r["id"] == "N19"] == ["%s without an etcd service" % kube["etcd_binary"]]


def test_n10_loopback_server_when_our_control_plane_has_no_ca_yet():
    cp1 = changed(lambda d: d["kube"].update(addresses=["192.0.2.10"]))
    w1 = changed(lambda d: d["kube"].update(kubelet_conf_server="https://[::1]:6443", kubelet_conf_ca="ca-a"))
    out = rules.node_preflight_evaluate([{"item": n, "stdout": json.dumps(d)} for n, d in (("cp1", cp1), ("w1", w1))],
                                        {**CTX, "api_hosts": ["cp1"], "control_plane": ["cp1"]})
    assert [(r["node"], r["status"], r["evidence"]) for r in out["rows"] if r["id"] == "N10"] == [
        ("cp1", "OK", "no other cluster's config"),
        ("w1", "FAIL", "kubelet.conf points at https://[::1]:6443 with another cluster's CA")]


PEM = "-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUQ0E=\n-----END CERTIFICATE-----\n"


def test_kubeconfig_ca_matches_the_cluster_ca(monkeypatch):
    data = base64.b64encode(PEM.encode()).decode()
    files = {"/etc/kubernetes/kubelet.conf": "clusters:\n- cluster:\n    certificate-authority-data: %s\n"
                                             "    server: https://127.0.0.1:6443\n" % data,
             "/etc/kubernetes/admin.conf": "    certificate-authority: /etc/kubernetes/ssl/ca.pem\n",
             "/etc/kubernetes/ssl/ca.pem": PEM, "/etc/kubernetes/pki/ca.crt": PEM}
    monkeypatch.setattr(probe, "read", lambda path, default="": files.get(path, default))
    monkeypatch.setattr(probe, "run", _fake_run({}))
    kube = probe.kube_leftovers(["/usr/local/bin"])
    assert kube["kubelet_conf_ca"] == kube["admin_conf_ca"] == kube["cluster_ca"] is not None


def test_each_kubeconfig_is_read_once(monkeypatch):
    reads = []
    monkeypatch.setattr(probe, "read", lambda path, default="": reads.append(path) or default)
    monkeypatch.setattr(probe, "run", _fake_run({}))
    probe.kube_leftovers(["/usr/local/bin"])
    assert reads.count("/etc/kubernetes/kubelet.conf") == reads.count("/etc/kubernetes/admin.conf") == 1


@pytest.mark.parametrize("text", ["", "no certificate here", "-----BEGIN CERTIFICATE-----\n@@@=\n-----END CERTIFICATE-----"])
def test_ca_hash_of_something_that_is_not_a_certificate(text):
    assert probe.ca_hash(text) is None


def test_addresses_from_ip_addr(monkeypatch):
    out = ("1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever\n"
           "1: lo    inet6 ::1/128 scope host noprefixroute \\       valid_lft forever preferred_lft forever\n"
           "2: eth0    inet 192.0.2.10/24 brd 192.0.2.255 scope global eth0\\       valid_lft forever\n"
           "2: eth0    inet6 fe80::1/64 scope link \\       valid_lft forever preferred_lft forever")
    monkeypatch.setattr(probe, "run", _fake_run({"ip": (0, out)}))
    assert probe.addresses() == ["127.0.0.1", "192.0.2.10", "::1", "fe80::1"]


SS = """LISTEN 0      4096       127.0.0.1:10248      0.0.0.0:* users:(("kubelet",pid=101,fd=20))
LISTEN 0      4096               *:10250            *:* users:(("kubelet",pid=101,fd=24))
LISTEN 0      4096            [::]:6443          [::]:* users:(("kube-apiserver",pid=202,fd=3))
LISTEN 0      4096       127.0.0.1:10257      0.0.0.0:* users:(("kube-controller",pid=303,fd=3))
LISTEN 0      4096         0.0.0.0:22         0.0.0.0:* users:(("sshd",pid=1,fd=3),("systemd",pid=1,fd=114))
LISTEN 0      128          0.0.0.0:2379       0.0.0.0:*"""


@pytest.mark.parametrize("answer", [(None, ""), (1, "ss: invalid option -- 'H'")])
def test_listeners_without_a_working_ss_is_a_probe_error(monkeypatch, answer):
    monkeypatch.setattr(probe, "run", _fake_run({"ss": answer}))
    with pytest.raises(RuntimeError, match="ports not checked"):
        probe.listeners({6443})


def test_listeners_lists_a_socket_on_both_families_once(monkeypatch):
    ss = ("LISTEN 0 4096 0.0.0.0:6443 0.0.0.0:* users:((\"nc\",pid=42,fd=3))\n"
          "LISTEN 0 4096 [::]:6443 [::]:* users:((\"nc\",pid=42,fd=4))")
    monkeypatch.setattr(probe, "run", _fake_run({"ss": (0, ss)}))
    assert probe.listeners({6443}) == [{"port": 6443, "proc": "nc", "pid": 42}]


def test_listeners_parses_ss(monkeypatch):
    monkeypatch.setattr(probe, "run", _fake_run({"ss": (0, SS)}))
    assert probe.listeners({6443, 10250, 10257, 2379}) == [
        {"port": 10250, "proc": "kubelet", "pid": 101},
        {"port": 6443, "proc": "kube-apiserver", "pid": 202},
        {"port": 10257, "proc": "kube-controller", "pid": 303},
        {"port": 2379, "proc": "?", "pid": None}]


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("answer, expected", [
    (200, {"ok": True}),
    (401, {"ok": True}),
    (403, {"ok": True, "refused": True}),
    (407, {"ok": False, "error": "proxy authentication required"}),
    (504, {"ok": False, "error": "HTTP 504 (the proxy could not reach it)"}),
    ("refused", {"ok": False, "error": "refused"}),
])
def test_reach_maps_answers(monkeypatch, answer, expected):
    def urlopen(url, timeout):
        if answer == 200:
            return _Response()
        if answer == "refused":
            raise probe.urllib.error.URLError("refused")
        raise probe.urllib.error.HTTPError(url, answer, "x", {}, None)
    monkeypatch.setattr(probe.urllib.request, "urlopen", urlopen)
    assert probe.reach(["https://example.com"]) == {"https://example.com": expected}


# What urllib raises when a proxy refuses the HTTPS tunnel (checked against a local proxy).
@pytest.mark.parametrize("reason, expected", [
    ("Tunnel connection failed: 403 Forbidden", {"ok": True, "refused": True}),
    ("Tunnel connection failed: 407 Proxy Authentication Required", {"ok": False, "error": "proxy authentication required"}),
    ("Tunnel connection failed: 502 Bad Gateway", {"ok": False, "error": "Tunnel connection failed: 502 Bad Gateway"}),
])
def test_reach_through_a_proxy_that_refuses_the_tunnel(monkeypatch, reason, expected):
    def urlopen(url, timeout):
        raise probe.urllib.error.URLError(OSError(reason))
    monkeypatch.setattr(probe.urllib.request, "urlopen", urlopen)
    assert probe.reach(["https://example.com"]) == {"https://example.com": expected}


def test_reach_reports_a_bad_http_answer(monkeypatch):
    def urlopen(url, timeout):
        raise probe.http.client.BadStatusLine("garbage")
    monkeypatch.setattr(probe.urllib.request, "urlopen", urlopen)
    assert probe.reach(["https://example.com"])["https://example.com"]["ok"] is False


def test_reach_reports_a_url_without_a_scheme():
    assert probe.reach(["github.com"]) == {"github.com": {"ok": False, "error": "not a URL, it needs https://"}}


@pytest.mark.parametrize("error, expected", [
    (ConnectionRefusedError, {"reachable": True, "via": "tcp/22"}),
    (TimeoutError, {"reachable": False, "via": None}),
])
def test_gateway_that_refuses_tcp_answered(monkeypatch, error, expected):
    monkeypatch.setattr(probe, "run", _fake_run({"ip": (0, "default via 192.0.2.1 dev eth0 proto static"),
                                                  "ping": (1, "100% packet loss")}))

    def connect(addr, timeout):
        raise error()
    monkeypatch.setattr(probe.socket, "create_connection", connect)
    assert probe.gateway() == {"ip": "192.0.2.1", "dev": "eth0", **expected}


# `ip -o route show default`, as iproute2 prints it.
@pytest.mark.parametrize("routes, ip, dev", [
    ("default via 192.0.2.1 dev eth0 proto static", "192.0.2.1", "eth0"),
    ("default \tnexthop via 192.0.2.1 dev d0 weight 1 \tnexthop via 198.51.100.1 dev d1 weight 1 ", "192.0.2.1", "d0"),
    ("default via 198.51.100.1 dev d1 metric 50 \ndefault via 192.0.2.1 dev d0 metric 100", "198.51.100.1", "d1"),
    ("default dev d0 scope link ", None, "d0"),
    ("", None, None),
])
def test_gateway_reads_the_default_route(monkeypatch, routes, ip, dev):
    monkeypatch.setattr(probe, "run", _fake_run({"ip": (0, routes), "ping": (0, "")}))
    out = probe.gateway()
    assert (out["ip"], out["dev"], out["reachable"]) == (ip, dev, ip is not None)


# `ip neigh show <gateway> dev <dev>`, as iproute2 prints it.
@pytest.mark.parametrize("neigh, reachable", [
    ("192.0.2.1 lladdr 16:18:2f:83:38:dc REACHABLE ", True),
    ("192.0.2.1 lladdr aa:bb:cc:dd:ee:01 PERMANENT ", True),
    ("192.0.2.1 lladdr aa:bb:cc:dd:ee:01 STALE ", False),
    ("192.0.2.1 lladdr aa:bb:cc:dd:ee:01 PROBE ", False),
    ("192.0.2.1 INCOMPLETE ", False),
    ("192.0.2.1 FAILED ", False),
    ("192.0.2.10 lladdr aa:bb:cc:dd:ee:09 REACHABLE ", False),
    ("192.0.2.1 lladdr aa:bb:cc:dd:ee:01 extern_learn NOARP proto zebra ", True),
    ("192.0.2.1 lladdr aa:bb:cc:dd:ee:01 STALE proto zebra ", False),
    ("", False),
])
def test_gateway_that_drops_pings_but_answers_arp(monkeypatch, neigh, reachable):
    answers = {("ip", "-o"): (0, "default via 192.0.2.1 dev eth0 proto static"), ("ip", "neigh"): (0, neigh),
               ("ping",): (1, "100% packet loss")}
    calls = []

    def run(args, timeout=10, stderr=True):
        calls.append(args)
        return answers.get(tuple(args[:2]), answers.get(tuple(args[:1])))
    monkeypatch.setattr(probe, "run", run)

    def connect(addr, timeout):
        raise TimeoutError()
    monkeypatch.setattr(probe.socket, "create_connection", connect)
    out = probe.gateway()
    assert (out["reachable"], out["via"]) == (reachable, "arp" if reachable else None)
    assert ["ip", "neigh", "show", "192.0.2.1", "dev", "eth0"] in calls


def test_kubeconfig_server_may_be_quoted(monkeypatch):
    confs = {"/etc/kubernetes/kubelet.conf": 'clusters:\n- cluster:\n    server: "https://192.0.2.10:6443"\n',
             "/etc/kubernetes/admin.conf": "    server: 'https://[fd00::10]:6443'\n"}
    monkeypatch.setattr(probe, "read", lambda path, default="": confs.get(path, default))
    monkeypatch.setattr(probe, "run", _fake_run({}))
    kube = probe.kube_leftovers(["/usr/local/bin"])
    assert (kube["kubelet_conf_server"], kube["admin_conf_server"]) == ("https://192.0.2.10:6443",
                                                                        "https://[fd00::10]:6443")


def _lsblk(node):
    return json.dumps({"blockdevices": [node]})


def test_ceph_device_reads_lsblk_and_the_bluestore_labels(tmp_path, monkeypatch):
    disk = tmp_path / "nvme1n1"
    with open(disk, "wb") as f:
        f.truncate(2 * GIB)
        f.seek(GIB)
        f.write(probe.BLUESTORE_LABEL)
    monkeypatch.setattr(probe, "run", _fake_run({"lsblk": (0, _lsblk(
        {"name": "nvme1n1", "type": "disk", "size": 2 * GIB, "fstype": None, "mountpoint": None})) }))
    assert probe.ceph_device(str(disk)) == {"exists": True, "type": "disk", "fstype": None, "children": [],
                                            "mountpoints": [], "bluestore_labels_gib": [1]}


def test_ceph_device_reports_partitions_and_mounts(tmp_path, monkeypatch):
    disk = tmp_path / "sda"
    disk.write_bytes(b"")
    monkeypatch.setattr(probe, "run", _fake_run({"lsblk": (0, _lsblk(
        {"name": "sda", "type": "disk", "size": 0, "fstype": None, "mountpoint": None,
         "children": [{"name": "sda1", "mountpoint": "/boot"}, {"name": "sda2", "mountpoint": "/"}]})) }))
    assert probe.ceph_device(str(disk)) == {"exists": True, "type": "disk", "fstype": None,
                                            "children": ["sda1", "sda2"], "mountpoints": ["/", "/boot"],
                                            "bluestore_labels_gib": []}


def test_ceph_device_missing_or_unreadable(tmp_path, monkeypatch):
    assert probe.ceph_device(str(tmp_path / "nvme9n9")) == {"exists": False}
    disk = tmp_path / "nvme1n1"
    disk.write_bytes(b"")
    monkeypatch.setattr(probe, "run", _fake_run({"lsblk": (32, "lsblk: not a block device")}))
    assert probe.ceph_device(str(disk)) == {"exists": True, "error": "lsblk: lsblk: not a block device"}


ALL_BUT_N15 = ["N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N10", "N11", "N12", "N13", "N14", "N16", "N19"]


@pytest.mark.parametrize("skip, has_os", [(ALL_BUT_N15, True), (ALL_BUT_N15 + ["N15"], False)])
def test_os_release_runs_only_for_n15(monkeypatch, capsys, skip, has_os):
    monkeypatch.setenv("NODE_PREFLIGHT_SPEC", json.dumps({"skip": skip}))
    probe.main()
    assert ("os" in json.loads(capsys.readouterr().out)) is has_os


def render(source, variables):
    return Templar(loader=DataLoader(), variables=variables).template(trust_as_template(source))


@pytest.mark.parametrize("nodes, workers, inventory, new", [
    ([{"hostname": "cp1"}], None, ["localhost"], ["cp1"]),
    ([{"hostname": "cp1"}], [{"hostname": "w1"}], ["localhost", "cp1"], ["w1"]),
    ([{"hostname": "cp1"}], [{"hostname": "w1"}], ["localhost", "cp1", "w1"], []),
    (None, None, ["localhost"], []),
])
def test_preflight_waits_until_the_inventory_has_the_nodes_yaml_nodes(nodes, workers, inventory, new):
    tasks = yaml.safe_load((_ROOT / "roles/kubernetes/tasks/install.yaml").read_text())
    task = next(t for t in tasks if t.get("name") == "kubernetes | Node preflight")
    variables = {"nodes_control_plane": nodes, "nodes_workers": workers, "groups": {"all": inventory}}
    assert render(task["vars"]["_np_new"], variables) == new


@pytest.mark.parametrize("node, expected", [
    ({"ansible_python_interpreter": "/usr/libexec/platform-python"}, "/usr/libexec/platform-python"),
    ({"ansible_facts": {"discovered_interpreter_python": "/usr/libexec/platform-python"}}, "/usr/libexec/platform-python"),
    ({"ansible_facts": {}}, "python3"),
])
def test_probe_runs_with_the_nodes_python(node, expected):
    task = yaml.safe_load((_ROOT / "roles/kubernetes/tasks/node_preflight.yaml").read_text())[1]
    assert render(task["ansible.builtin.script"]["executable"], {"item": "n1", "hostvars": {"n1": node}}) == expected
