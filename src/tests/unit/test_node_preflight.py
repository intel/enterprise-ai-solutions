# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
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
CLEAN_DISK = {"exists": True, "type": "disk", "fstype": None, "children": [], "mountpoints": [],
              "bluestore_labels_gib": []}


def healthy():
    return {
        "node_epoch": 1000.0, "controller_epoch": 999.6, "etcd_node": False,
        "time": {"synced": True, "detail": "chrony: Leap status Normal"},
        "disk": {"root": {"path": "/", "avail": 200 * GIB, "used_pct": 30.0},
                 "containerd": {"path": "/var/lib", "avail": 200 * GIB, "used_pct": 30.0}},
        "urls": {"https://github.com": None, "https://registry.k8s.io/v2/": None},
        "kube": {"kubelet_active": False, "kubeconfigs": {}, "manifests": [], "etcd_member": False,
                 "etcd_binary": None, "etcd_unit": False, "cni_calico": []},
        "firewall": {"ufw": False, "firewalld": False},
        "ceph": {},
    }


def evaluate(data, skip=()):
    return rules.node_preflight_evaluate([{"item": "n1", "stdout": json.dumps(data)}], list(skip))


def status(data, cid):
    found = [c[1] for c in rules._checks(data) if c[0] == cid]
    return found[0] if found else None


def changed(fn):
    data = copy.deepcopy(healthy())
    fn(data)
    return data


def test_healthy_node_has_no_failures_or_warnings():
    out = evaluate(healthy())
    assert out == {"failures": [], "table": ["n1: 7 ok, 0 warn, 0 fail"]}


CASES = [
    # name, change to the healthy probe, check, expected status
    ("N1 not synced", lambda d: d["time"].update(synced=False), "N1", "FAIL"),
    ("N2 12 s", lambda d: d.update(controller_epoch=988.0), "N2", "WARN"),
    ("N2 -90 s", lambda d: d.update(controller_epoch=1090.0), "N2", "FAIL"),
    ("N3 15 GiB free", lambda d: d["disk"]["containerd"].update(avail=15 * GIB), "N3", "FAIL"),
    ("N3 35 GiB free", lambda d: d["disk"]["containerd"].update(avail=35 * GIB), "N3", "WARN"),
    ("N3 95% used", lambda d: d["disk"]["containerd"].update(used_pct=95.0), "N3", "FAIL"),
    ("N4 root 92% used", lambda d: d["disk"]["root"].update(used_pct=92.0), "N4", "FAIL"),
    ("N8 timeout", lambda d: d["urls"].update({"https://github.com": "timed out"}), "N8", "FAIL"),
    ("N11 Calico CNI config", lambda d: d["kube"].update(cni_calico=["10-calico.conflist"]), "N11", "WARN"),
    ("N11 kubeconfig of a cluster",
     lambda d: d["kube"].update(kubeconfigs={"kubelet.conf": "https://198.51.100.9:6443"}), "N11", "WARN"),
    ("N14 ufw active", lambda d: d["firewall"].update(ufw=True), "N14", "WARN"),
    ("N16 clean disk", lambda d: d.update(ceph={"/dev/nvme1n1": CLEAN_DISK}), "N16", "OK"),
    ("N16 missing disk", lambda d: d.update(ceph={"/dev/nvme9n9": {"exists": False}}), "N16", "FAIL"),
    ("N16 OS disk", lambda d: d.update(ceph={"/dev/sda": {**CLEAN_DISK, "children": ["sda1"], "mountpoints": ["/"]}}),
     "N16", "FAIL"),
    ("N16 old BlueStore label", lambda d: d.update(ceph={"/dev/nvme1n1": {**CLEAN_DISK, "bluestore_labels_gib": [10]}}),
     "N16", "FAIL"),
    ("N19 not an etcd node", lambda d: d["kube"].update(etcd_binary="/usr/local/bin/etcd"), "N19", None),
    ("N19 binary without a service",
     lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary="/usr/local/bin/etcd")), "N19", "FAIL"),
    ("N19 installed etcd",
     lambda d: (d.update(etcd_node=True), d["kube"].update(etcd_binary="/usr/local/bin/etcd", etcd_unit=True)),
     "N19", "OK"),
]


@pytest.mark.parametrize("change, cid, expected", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_check(change, cid, expected):
    assert status(changed(change), cid) == expected


def test_failures_name_the_node_the_evidence_and_a_fix():
    out = evaluate(changed(lambda d: d["disk"]["root"].update(used_pct=92.0)))
    assert [(r["node"], r["id"], r["status"], r["evidence"]) for r in out["failures"]] == [
        ("n1", "N4", "FAIL", "92.0% used, 200 GiB free on /")]
    assert out["failures"][0]["hint"]


def test_a_skipped_check_is_left_out():
    data = changed(lambda d: (d["disk"]["containerd"].update(avail=15 * GIB), d.pop("urls")))
    out = evaluate(data, skip=["N3", "N8"])
    assert out["failures"] == [] and out["table"] == ["n1: 5 ok, 0 warn, 0 fail"]


def test_sudo_warnings_before_the_probe_json_are_ignored():
    stdout = "sudo: unable to resolve host n1: Name or service not known\r\n" + json.dumps(healthy()) + "\r\n"
    assert rules.node_preflight_evaluate([{"item": "n1", "stdout": stdout}], [])["failures"] == []


def _fake_run(answers):
    return lambda args: answers.get(args[0], (None, ""))


@pytest.mark.parametrize("answers, expected", [
    ({"chronyc": (0, "Reference ID    : A9FEA97B\nLeap status     : Normal")},
     {"synced": True, "detail": "chrony: Leap status Normal"}),
    # chronyd stopped, or systemd-timesyncd instead (Ubuntu's default).
    ({"chronyc": (1, "506 Cannot talk to daemon"), "timedatectl": (0, "no")},
     {"synced": False, "detail": "timedatectl: NTPSynchronized=no"}),
    ({"timedatectl": (0, "yes")}, {"synced": True, "detail": "timedatectl: NTPSynchronized=yes"}),
])
def test_time_sync(monkeypatch, answers, expected):
    monkeypatch.setattr(probe, "run", _fake_run(answers))
    assert probe.time_sync() == expected


def test_disk_uses_the_nearest_existing_parent(tmp_path):
    out = probe.disk(str(tmp_path / "not" / "there"))
    assert out["path"] == str(tmp_path) and out["avail"] > 0 and 0 <= out["used_pct"] <= 100


@pytest.mark.parametrize("error, expected", [
    (None, None),
    # registry.k8s.io answers 401: the site was reached.
    (probe.urllib.error.HTTPError("https://registry.k8s.io/v2/", 401, "Unauthorized", {}, None), None),
    # What urllib raises when the proxy refuses the HTTPS tunnel, or the node has no route.
    (probe.urllib.error.URLError(OSError("Tunnel connection failed: 403 Forbidden")),
     "Tunnel connection failed: 403 Forbidden"),
    (probe.urllib.error.URLError(ConnectionRefusedError(111, "Connection refused")), "[Errno 111] Connection refused"),
])
def test_reach(monkeypatch, error, expected):
    class Response:
        def close(self):
            pass

    def urlopen(url, timeout):
        if error:
            raise error
        return Response()
    monkeypatch.setattr(probe.urllib.request, "urlopen", urlopen)
    assert probe.reach() == {url: expected for url in probe.URLS}


def test_kube_leftovers(tmp_path, monkeypatch):
    confs = {"/etc/kubernetes/kubelet.conf": "clusters:\n- cluster:\n    certificate-authority-data: LS0t\n"
                                             "    server: https://127.0.0.1:6443\n  name: cluster.local\n"}
    monkeypatch.setattr(probe, "read", lambda path: confs.get(path, ""))
    monkeypatch.setattr(probe, "run", _fake_run({"systemctl": (3, "inactive")}))
    (tmp_path / "etcd").write_text("")
    kube = probe.kube_leftovers(str(tmp_path))
    assert (kube["kubeconfigs"], kube["kubelet_active"], kube["etcd_binary"]) == (
        {"kubelet.conf": "https://127.0.0.1:6443"}, False, str(tmp_path / "etcd"))


@pytest.mark.parametrize("answers, expected", [
    ({"ufw": (0, "Status: active\n\nTo  Action  From"), "systemctl": (3, "inactive")},
     {"ufw": True, "firewalld": False}),
    ({"systemctl": (0, "active")}, {"ufw": False, "firewalld": True}),
])
def test_firewall(monkeypatch, answers, expected):
    monkeypatch.setattr(probe, "run", _fake_run(answers))
    assert probe.firewall() == expected


def _lsblk(node):
    return 0, json.dumps({"blockdevices": [node]})


def test_ceph_device_reads_lsblk_and_the_bluestore_labels(tmp_path, monkeypatch):
    disk = tmp_path / "nvme1n1"
    with open(disk, "wb") as f:
        f.truncate(2 * GIB)
        f.seek(GIB)
        f.write(probe.BLUESTORE_LABEL)
    monkeypatch.setattr(probe, "run", _fake_run({"lsblk": _lsblk(
        {"name": "nvme1n1", "type": "disk", "size": 2 * GIB, "fstype": None, "mountpoint": None})}))
    assert probe.ceph_device(str(disk)) == {"exists": True, "type": "disk", "fstype": None, "children": [],
                                            "mountpoints": [], "bluestore_labels_gib": [1]}


def test_ceph_device_reports_partitions_and_mounts(tmp_path, monkeypatch):
    disk = tmp_path / "sda"
    disk.write_bytes(b"")
    monkeypatch.setattr(probe, "run", _fake_run({"lsblk": _lsblk(
        {"name": "sda", "type": "disk", "size": 0, "fstype": None, "mountpoint": None,
         "children": [{"name": "sda1", "mountpoint": "/boot"}, {"name": "sda2", "mountpoint": "/"}]})}))
    assert probe.ceph_device(str(disk)) == {"exists": True, "type": "disk", "fstype": None,
                                            "children": ["sda1", "sda2"], "mountpoints": ["/", "/boot"],
                                            "bluestore_labels_gib": []}


def test_ceph_device_missing(tmp_path):
    assert probe.ceph_device(str(tmp_path / "nvme9n9")) == {"exists": False}


def test_skipping_n8_skips_its_urls(monkeypatch, capsys):
    monkeypatch.setattr(probe, "reach", lambda: pytest.fail("N8 is skipped"))
    spec = {"controller_epoch": 1.0, "etcd": False, "skip": ["N8"], "bin_dir": "/usr/local/bin", "ceph_devices": []}
    monkeypatch.setenv("NODE_PREFLIGHT_SPEC", json.dumps(spec))
    probe.main()
    data = json.loads(capsys.readouterr().out)
    assert "urls" not in data and "N8" not in [c[0] for c in rules._checks(data)]
