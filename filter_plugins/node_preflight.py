# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Node preflight rules: the probe's JSON from each node becomes failures and a table.

FAIL stops the install, WARN is printed. The check IDs are what kubernetes_node_preflight_skip takes.
"""

import json
from urllib.parse import urlparse

GIB = 1 << 30
TITLES = {
    "N1": "Time sync", "N2": "Clock skew", "N3": "Disk for images", "N4": "Root disk", "N8": "Internet",
    "N11": "Leftovers", "N14": "Firewall", "N16": "Ceph disks", "N19": "Half-removed etcd",
}
MIN_FREE_GIB, WARN_FREE_GIB, MAX_USED_PCT = 20, 50, 90
MAX_SKEW_S, WARN_SKEW_S = 60, 5


def _gib(n):
    return "%.0f GiB" % (n / GIB)


def _time_checks(data):
    sync = data["time"]
    skew = data["node_epoch"] - float(data["controller_epoch"])
    return [("N1", "OK" if sync["synced"] else "FAIL", sync["detail"],
             "Enable time sync: sudo systemctl enable --now chrony (or systemd-timesyncd), "
             "then check chronyc tracking"),
            ("N2", "FAIL" if abs(skew) > MAX_SKEW_S else "WARN" if abs(skew) > WARN_SKEW_S else "OK",
             "%+.1f s against the deploy node (includes SSH latency)" % skew,
             "Point every node and the deploy node at the same NTP servers")]


def _disk_checks(disk):
    images, root = disk["containerd"], disk["root"]
    full = images["used_pct"] > MAX_USED_PCT
    status = ("FAIL" if images["avail"] < MIN_FREE_GIB * GIB or full else
              "WARN" if images["avail"] < WARN_FREE_GIB * GIB else "OK")
    need = "get it below %d%% used" % MAX_USED_PCT if full else "free at least %d GiB there" % MIN_FREE_GIB
    return [("N3", status, "%s free, %s%% used on %s" % (_gib(images["avail"]), images["used_pct"], images["path"]),
             "Container images need space under /var/lib/containerd: %s, or mount a larger disk" % need),
            ("N4", "FAIL" if root["used_pct"] > MAX_USED_PCT else "OK",
             "%s%% used, %s free on /" % (root["used_pct"], _gib(root["avail"])), "Free space on /")]


def _internet_checks(urls):
    hosts = {url: urlparse(url).hostname for url in urls}
    bad = ["%s (%s)" % (hosts[url], err) for url, err in sorted(urls.items()) if err]
    return [("N8", "FAIL" if bad else "OK",
             "cannot reach " + ", ".join(bad) if bad else "reaches " + ", ".join(sorted(hosts.values())),
             "Kubespray downloads on every node: set http_proxy/https_proxy/no_proxy in global_config.yaml, "
             "or open HTTPS. Nodes that download from a mirror skip this check (kubernetes_node_preflight_skip: [N8])")]


def _leftover_checks(kube, etcd_node):
    left = ["%s points at %s" % (conf, server) for conf, server in sorted(kube["kubeconfigs"].items())]
    if kube["kubelet_active"]:
        left.append("kubelet running")
    if kube["manifests"]:
        left.append("/etc/kubernetes/manifests: " + ", ".join(kube["manifests"]))
    if kube["etcd_member"]:
        left.append("/var/lib/etcd/member")
    if kube["cni_calico"]:
        left.append("/etc/cni/net.d: " + ", ".join(kube["cni_calico"]))
    if kube["etcd_binary"] and kube["etcd_unit"]:
        left.append("etcd installed")
    rows = [("N11", "WARN" if left else "OK", "; ".join(left) or "none",
             "Left from an earlier install. A re-run usually reuses it. If the node was in another cluster, "
             "remove it there and clean it (run teardown, or kubeadm reset) before installing")]
    if etcd_node:
        half = kube["etcd_binary"] and not kube["etcd_unit"]
        rows.append(("N19", "FAIL" if half else "OK",
                     "%s without an etcd service" % kube["etcd_binary"] if half else "no half-removed etcd",
                     "Left by an unfinished reset: Kubespray takes it for an installed etcd and fails at "
                     "'Stop etcd'. Run the teardown (Kubespray reset) again, or remove etcd, etcdctl and etcdutl "
                     "from that directory and /etc/etcd.env on this node"))
    return rows


def _firewall_checks(firewall):
    active = [name for name in ("ufw", "firewalld") if firewall[name]]
    return [("N14", "WARN" if active else "OK", ("active: " + ", ".join(active)) if active else "none active",
             "Kubernetes needs its ports open between nodes (and 179 for BGP): open them, or disable the firewall")]


def _disk_problems(info):
    if not info.get("exists"):
        return ["not found"]
    why = [info["error"]] if info.get("error") else []
    if info.get("type") and info["type"] != "disk":
        why.append("is a %s, not a whole disk" % info["type"])
    if info.get("mountpoints"):
        why.append("mounted at " + ", ".join(info["mountpoints"]))
    if info.get("children"):
        why.append("has partitions " + ", ".join(info["children"]))
    if info.get("fstype"):
        why.append("has a %s signature" % info["fstype"])
    if info.get("bluestore_labels_gib"):
        why.append("old Ceph BlueStore label at %s GiB" % ", ".join(str(g) for g in info["bluestore_labels_gib"]))
    return why


def _ceph_checks(devices):
    problems = []
    for dev, info in sorted(devices.items()):
        why = _disk_problems(info)
        if why:
            problems.append("%s %s" % (dev, "; ".join(why)))
    return [("N16", "FAIL" if problems else "OK", " | ".join(problems) or
             "%d disk(s) ready: %s" % (len(devices), ", ".join(sorted(devices))),
             "Rook skips a disk that is mounted, partitioned or carries a signature, which leaves 0 OSDs: "
             "check the device names in rack-profile.yaml / rook_ceph_devices, and wipe a former OSD disk "
             "(docs/quickstart/prerequisites.md, Reusing a disk that held an OSD). Teardown keeps OSD data "
             "unless rook_ceph_teardown_wipe_devices is true")]


def _checks(data):
    """(id, status, evidence, hint) for one node's probe data."""
    rows = (_time_checks(data) + _disk_checks(data["disk"]) + _leftover_checks(data["kube"], data["etcd_node"])
            + _firewall_checks(data["firewall"]))
    if "urls" in data:  # not probed when N8 is skipped
        rows += _internet_checks(data["urls"])
    if data["ceph"]:
        rows += _ceph_checks(data["ceph"])
    return rows


def node_preflight_evaluate(results, skip):
    failures, table = [], []
    for res in results:
        # The probe's JSON is its last line. With ssh -tt, sudo's warnings ("unable to resolve host") come first.
        data = json.loads([line for line in res["stdout"].splitlines() if line.strip().startswith("{")][-1])
        rows = [{"node": res["item"], "id": cid, "check": TITLES[cid], "status": status, "evidence": evidence,
                 "hint": hint} for cid, status, evidence, hint in _checks(data) if cid not in skip]
        counts = {s: sum(1 for r in rows if r["status"] == s) for s in ("OK", "WARN", "FAIL")}
        table.append("%s: %d ok, %d warn, %d fail" % (res["item"], counts["OK"], counts["WARN"], counts["FAIL"]))
        table += ["  %s %-4s %-17s %s" % (r["id"], r["status"], r["check"], r["evidence"])
                  for r in rows if r["status"] != "OK"]
        failures += [r for r in rows if r["status"] == "FAIL"]
    return {"failures": failures, "table": table}


class FilterModule:
    def filters(self):
        return {"node_preflight_evaluate": node_preflight_evaluate}
