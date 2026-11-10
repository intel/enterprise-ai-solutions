# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import copy
import importlib.util
import json
import pathlib

import pytest

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
        "urls": {"https://github.com": {"ok": True, "status": 200},
                 "https://registry.k8s.io/v2/": {"ok": True, "status": 401}},
        "kube": {"kubelet_active": False, "kubelet_conf_server": None, "admin_conf_server": None, "manifests": [],
                 "etcd_member": False, "etcd_binary": False, "etcd_unit": False, "cni_calico": []},
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
    ("N7 name fails, no proxy", lambda d: d["dns"]["quay.io"].update(ok=False), {}, "N7", "FAIL"),
    ("N7 name fails behind a proxy", lambda d: d["dns"]["quay.io"].update(ok=False),
     {"proxy_host": "proxy.example.com"}, "N7", "WARN"),
    ("N7 name fails, internet check skipped", lambda d: d["dns"]["quay.io"].update(ok=False), {"skip": ["N8"]},
     "N7", "WARN"),
    ("N7 proxy host fails", lambda d: d["dns"].update({"proxy.example.com": {"ok": False}}),
     {"proxy_host": "proxy.example.com"}, "N7", "FAIL"),
    ("N8 timeout", lambda d: d["urls"]["https://github.com"].update(ok=False, status=None, error="timed out"),
     {}, "N8", "FAIL"),
    ("N8 proxy 407", lambda d: d["urls"]["https://github.com"].update(ok=False, status=407), {}, "N8", "FAIL"),
    ("N8 skipped", lambda d: d["urls"]["https://github.com"].update(ok=False), {"skip": ["N8"]}, "N8", "SKIP"),
    ("N9 not root after become", lambda d: d.update(euid=1000), {}, "N9", "FAIL"),
    ("N10 kubelet.conf of another cluster", lambda d: d["kube"].update(kubelet_conf_server="https://198.51.100.9:6443"),
     {}, "N10", "FAIL"),
    ("N10 our control plane", lambda d: d["kube"].update(kubelet_conf_server="https://192.0.2.10:6443"),
     {}, "N10", "OK"),
    ("N10 worker local proxy", lambda d: d["kube"].update(kubelet_conf_server="https://localhost:6443"),
     {}, "N10", "OK"),
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
    ("N19 not an etcd node", lambda d: d["kube"].update(etcd_binary=True), {}, "N19", []),
    ("N19 binary without a service", lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary=True)),
     {}, "N19", "FAIL"),
    ("N19 installed etcd", lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary=True, etcd_unit=True)),
     {}, "N19", "OK"),
    ("N11 installed etcd is a leftover",
     lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary=True, etcd_unit=True)), {}, "N11", "WARN"),
    ("N19 clean etcd node", lambda d: d.update(etcd_node=True), {}, "N19", "OK"),
    ("N0 probe error", lambda d: d.update(errors=["dns: boom"]), {}, "N0", "WARN"),
]


@pytest.mark.parametrize("change, ctx, cid, expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_check(change, ctx, cid, expected):
    assert status(changed(change), cid, **ctx) == expected


@pytest.mark.parametrize("result, cid", [
    ({"failed": True, "msg": "Missing sudo password"}, "N9"),
    ({"failed": True, "msg": "/bin/sh: python3: not found", "rc": 127}, "N0"),
])
def test_probe_that_could_not_run(result, cid):
    out = rules.node_preflight_evaluate([{"item": "n1", "stdout": "", **result}], CTX)
    assert [(r["id"], r["status"]) for r in out["rows"]] == [(cid, "FAIL")]


def _fake_run(answers):
    return lambda args, timeout=10: answers.get(args[0], (None, ""))


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
