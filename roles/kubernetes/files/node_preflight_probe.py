#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Node preflight probe: reads NODE_PREFLIGHT_SPEC, prints what it finds as JSON, changes nothing.

Standard library only, Python 3.6+ (RHEL 8 nodes).
"""

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request

GIB = 1 << 30
# Ceph writes a BlueStore label at these offsets; a stale one makes Rook skip the disk.
BLUESTORE_LABEL = b"bluestore block device"
BLUESTORE_OFFSETS_GIB = (0, 1, 10, 100, 1000)
URLS = ("https://github.com", "https://registry.k8s.io/v2/")


def run(args):
    path = shutil.which(args[0])
    if not path:
        return None, ""
    try:
        res = subprocess.run([path] + args[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=10, env=dict(os.environ, LC_ALL="C"))
        return res.returncode, res.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def time_sync():
    rc, out = run(["chronyc", "tracking"])
    leap = re.search(r"Leap status\s*:\s*(.+)", out) if rc == 0 else None
    if leap:
        status = leap.group(1).strip()
        return {"synced": status == "Normal", "detail": "chrony: Leap status " + status}
    _, out = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    return {"synced": out == "yes", "detail": "timedatectl: NTPSynchronized=" + (out or "?")}


def disk(path):
    while path != "/" and not os.path.exists(path):
        path = os.path.dirname(path)
    st = os.statvfs(path)
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    pct = 100.0 * used / (used + avail) if used + avail else 0.0
    return {"path": path, "avail": avail, "used_pct": round(pct, 1)}


def reach():
    """URL -> None when reached, else the error. HTTPS goes through the proxy in the environment.
    Any HTTP answer comes from the site (registry.k8s.io answers 401): a proxy that refuses
    the HTTPS tunnel raises URLError instead."""
    out = {}
    for url in URLS:
        out[url] = None
        try:
            # Fixed https URLs (URLS).
            urllib.request.urlopen(url, timeout=10).close()  # nosec B310
        except urllib.error.HTTPError:
            pass
        except OSError as exc:
            out[url] = str(getattr(exc, "reason", exc))
    return out


def kube_leftovers(bin_dir):
    _, out = run(["systemctl", "is-active", "kubelet"])
    servers = {}
    for conf in ("kubelet.conf", "admin.conf"):
        match = re.search(r"^\s*server:\s*(\S+)", read("/etc/kubernetes/" + conf), re.M)
        if match:
            servers[conf] = match.group(1)
    manifests = "/etc/kubernetes/manifests"
    cni = "/etc/cni/net.d"
    return {
        "kubelet_active": out == "active",
        "kubeconfigs": servers,
        "manifests": sorted(os.listdir(manifests)) if os.path.isdir(manifests) else [],
        "etcd_member": os.path.isdir("/var/lib/etcd/member"),
        "etcd_binary": bin_dir + "/etcd" if os.path.exists(bin_dir + "/etcd") else None,
        "etcd_unit": any(os.path.exists(d + "/etcd.service")
                         for d in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system")),
        "cni_calico": sorted(f for f in os.listdir(cni) if "calico" in f) if os.path.isdir(cni) else [],
    }


def firewall():
    ufw = run(["ufw", "status"])[1].startswith("Status: active")
    _, out = run(["systemctl", "is-active", "firewalld"])
    return {"ufw": ufw, "firewalld": out == "active"}


def ceph_device(dev):
    real = os.path.realpath(dev)
    if not os.path.exists(real):
        return {"exists": False}
    _, out = run(["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT", real])
    try:
        node = json.loads(out)["blockdevices"][0]
    except (ValueError, KeyError, IndexError, TypeError):
        return {"exists": True, "error": "lsblk: " + (out or "no output")}
    children = node.get("children") or []
    size = int(node.get("size") or 0)
    info = {"exists": True, "type": node.get("type"), "fstype": node.get("fstype"),
            "children": [c.get("name") for c in children],
            "mountpoints": sorted({m for m in [node.get("mountpoint")] + [c.get("mountpoint") for c in children] if m})}
    labels = []
    try:
        with open(real, "rb") as f:
            for off in BLUESTORE_OFFSETS_GIB:
                if off * GIB + len(BLUESTORE_LABEL) > size:
                    break
                f.seek(off * GIB)
                if f.read(len(BLUESTORE_LABEL)) == BLUESTORE_LABEL:
                    labels.append(off)
    except OSError as exc:
        info["error"] = "read: %s" % exc
    info["bluestore_labels_gib"] = labels
    return info


def main():
    spec = json.loads(os.environ["NODE_PREFLIGHT_SPEC"])
    out = {"node_epoch": time.time(), "controller_epoch": spec["controller_epoch"], "etcd_node": spec["etcd"],
           "time": time_sync(), "disk": {"root": disk("/"), "containerd": disk("/var/lib/containerd")},
           "kube": kube_leftovers(spec["bin_dir"]), "firewall": firewall(),
           "ceph": {d: ceph_device(d) for d in spec["ceph_devices"]}}
    # Nodes that download from a mirror skip N8: don't wait for its timeouts.
    if "N8" not in spec["skip"]:
        out["urls"] = reach()
    print(json.dumps(out))


if __name__ == "__main__":
    main()
