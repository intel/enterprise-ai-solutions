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
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request

GIB = 1 << 30
# Ceph writes a BlueStore label at these offsets; a stale one makes Rook skip the disk.
BLUESTORE_LABEL = b"bluestore block device"
BLUESTORE_OFFSETS_GIB = (0, 1, 10, 100, 1000)


def run(args, timeout=10):
    """(rc, output) of a command found on PATH; (None, '') when it isn't installed."""
    path = shutil.which(args[0])
    if not path:
        return None, ""
    try:
        res = subprocess.run([path] + list(args[1:]), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, timeout=timeout)
        return res.returncode, res.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)


def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def os_release():
    info = {}
    for line in read("/etc/os-release").splitlines():
        key, _, value = line.partition("=")
        info[key] = value.strip().strip('"')
    return {"id": info.get("ID", ""), "version_id": info.get("VERSION_ID", ""),
            "pretty": info.get("PRETTY_NAME", "")}


def time_sync():
    rc, out = run(["chronyc", "tracking"])
    if rc == 0:
        leap = re.search(r"Leap status\s*:\s*(.+)", out)
        status = leap.group(1).strip() if leap else (out.splitlines() or ["no output"])[0]
        return {"source": "chrony", "synced": status == "Normal", "detail": "Leap status: " + status}
    # chronyc fails when chronyd is stopped; the node may sync with another daemon.
    sync_rc, sync = run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if sync_rc is not None:
        return {"source": "timedatectl", "synced": sync == "yes", "detail": "NTPSynchronized=" + (sync or "?")}
    if rc is not None:
        return {"source": "chrony", "synced": False, "detail": (out.splitlines() or ["no output"])[0]}
    return {"source": "none", "synced": False, "detail": "neither chronyc nor timedatectl found"}


def disk(path):
    """df-style numbers for the filesystem holding path (or its nearest existing parent)."""
    while path != "/" and not os.path.exists(path):
        path = os.path.dirname(path)
    st = os.statvfs(path)
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    pct = 100.0 * used / (used + avail) if used + avail else 0.0
    return {"path": path, "avail": avail, "used_pct": round(pct, 1)}


def gateway():
    rc, out = run(["ip", "-o", "route", "show", "default"])
    match = re.search(r"default via (\S+)(?: dev (\S+))?", out or "")
    if not match:
        return {"ip": None, "dev": None, "reachable": False, "via": None}
    gw, dev = match.group(1), match.group(2)
    result = {"ip": gw, "dev": dev, "reachable": False, "via": None}
    rc, _ = run(["ping", "-c", "2", "-W", "2", gw], timeout=10)
    if rc == 0:
        result.update(reachable=True, via="icmp")
        return result
    for port in (22, 443):
        try:
            socket.create_connection((gw, port), timeout=3).close()
            result.update(reachable=True, via="tcp/%d" % port)
            return result
        except OSError:
            continue
    return result


def resolve(names, timeout=5.0):
    """getaddrinfo per name, each bounded by a timeout (a broken resolver can hang)."""
    found = {}

    def one(name):
        try:
            socket.getaddrinfo(name, 443)
            found[name] = {"ok": True}
        except OSError as exc:
            found[name] = {"ok": False, "error": str(exc)}

    threads = [threading.Thread(target=one, args=(n,), daemon=True) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout)
    return {n: found.get(n, {"ok": False, "error": "timed out after %ss" % timeout}) for n in names}


def reach(urls, timeout=10):
    """HTTPS through the proxy from the environment. A 4xx answer still means the site
    was reached (registry.k8s.io answers 401); 407 and 5xx mean it wasn't: a proxy
    answers 502/504 itself when it can't reach the site."""
    out = {}
    for url in urls:
        try:
            # Fixed https URLs from the spec (kubernetes_node_preflight_urls).
            with urllib.request.urlopen(url, timeout=timeout):  # nosec B310
                out[url] = {"ok": True}
        except urllib.error.HTTPError as exc:
            if exc.code == 407:
                out[url] = {"ok": False, "error": "proxy authentication required"}
            elif exc.code >= 500:
                out[url] = {"ok": False, "error": "HTTP %d (the proxy could not reach it)" % exc.code}
            else:
                out[url] = {"ok": True}
        except (urllib.error.URLError, OSError) as exc:
            out[url] = {"ok": False, "error": str(getattr(exc, "reason", exc))}
    return out


def addresses():
    """The node's own IP addresses. Kubespray binds the API server to one of them,
    the default-route address when the inventory sets no ip."""
    rc, out = run(["ip", "-o", "addr", "show"])
    return sorted(set(re.findall(r"\binet6?\s+([0-9A-Fa-f:.]+)/", out or "")))


def kube_leftovers():
    def server(conf):
        match = re.search(r"^\s*server:\s*(\S+)", read(conf), re.M)
        return match.group(1) if match else None

    rc, out = run(["systemctl", "is-active", "kubelet"])
    manifests = "/etc/kubernetes/manifests"
    cni = "/etc/cni/net.d"
    return {
        "kubelet_active": out == "active",
        "kubelet_conf_server": server("/etc/kubernetes/kubelet.conf"),
        "admin_conf_server": server("/etc/kubernetes/admin.conf"),
        "manifests": sorted(os.listdir(manifests)) if os.path.isdir(manifests) else [],
        "etcd_member": os.path.isdir("/var/lib/etcd/member"),
        # An etcd binary without its unit (a half-finished reset) makes Kubespray
        # treat etcd as installed and fail at "Stop etcd".
        "etcd_binary": os.path.exists("/usr/local/bin/etcd"),
        "etcd_unit": any(os.path.exists(d + "/etcd.service")
                         for d in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system")),
        "cni_calico": sorted(f for f in os.listdir(cni) if "calico" in f) if os.path.isdir(cni) else [],
        "addresses": addresses(),
    }


def listeners(ports):
    """Owners of the listening TCP sockets on the given ports (needs root for names)."""
    rc, out = run(["ss", "-H", "-ltnp"])
    found = []
    for line in (out or "").splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        port = cols[3].rsplit(":", 1)[-1]
        if not port.isdigit() or int(port) not in ports:
            continue
        proc = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        found.append({"port": int(port), "proc": proc.group(1) if proc else "?",
                      "pid": int(proc.group(2)) if proc else None})
    return found


def package_manager():
    busy = sorted({read("/proc/%s/comm" % pid) for pid in os.listdir("/proc") if pid.isdigit()}
                  & {"apt", "apt-get", "dpkg", "unattended-upgr", "unattended-upgrade"})
    rc, out = run(["dpkg", "--audit"], timeout=30)
    return {"dpkg_audit": None if rc is None else out, "busy": busy}


def firewall():
    rc, out = run(["ufw", "status"])
    ufw = None if rc is None else out.startswith("Status: active")
    rc, out = run(["systemctl", "is-active", "firewalld"])
    return {"ufw": ufw, "firewalld": out == "active"}


def ceph_device(dev):
    """Whether dev can become an OSD: a whole, unmounted, unpartitioned disk with no signature."""
    real = os.path.realpath(dev)
    if not os.path.exists(real):
        return {"exists": False}
    rc, out = run(["lsblk", "-J", "-b", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINT", real])
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
    spec = json.loads(os.environ.get("NODE_PREFLIGHT_SPEC", "{}"))
    skip = set(spec.get("skip", []))
    out = {"node_epoch": time.time(), "controller_epoch": spec.get("controller_epoch"),
           "etcd_node": bool(spec.get("etcd")), "euid": os.geteuid()}
    probes = [
        ("os", ("N15",), os_release),
        ("time", ("N1", "N2"), time_sync),
        ("disk", ("N3", "N4", "N5"), lambda: {
            "root": disk("/"), "containerd": disk("/var/lib/containerd"),
            "etcd": disk("/var/lib/etcd") if spec.get("etcd") else None}),
        ("gateway", ("N6",), gateway),
        ("dns", ("N7",), lambda: resolve(spec.get("dns_names", []))),
        ("urls", ("N8",), lambda: reach(spec.get("urls", []))),
        ("kube", ("N10", "N11", "N19"), kube_leftovers),
        ("ports", ("N11", "N12"), lambda: listeners(set(spec.get("ports", [])))),
        ("pkg", ("N13",), package_manager),
        ("firewall", ("N14",), firewall),
        ("ceph", ("N16",), lambda: {d: ceph_device(d) for d in spec.get("ceph_devices", [])}),
    ]
    errors = []
    for key, ids, probe in probes:
        if skip.issuperset(ids):
            continue
        try:
            out[key] = probe()
        except Exception as exc:  # one broken probe must not hide the other results
            errors.append("%s: %s" % (key, exc))
    out["errors"] = errors
    print(json.dumps(out))


if __name__ == "__main__":
    main()
