#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Node preflight probe: reads NODE_PREFLIGHT_SPEC, prints what it finds as JSON, changes nothing.

Standard library only, Python 3.6+ (RHEL 8 nodes).
"""

import base64
import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request

GIB = 1 << 30
# Ceph writes a BlueStore label at these offsets; a stale one makes Rook skip the disk.
BLUESTORE_LABEL = b"bluestore block device"
BLUESTORE_OFFSETS_GIB = (0, 1, 10, 100, 1000)
# The cluster CA on a control-plane node: Kubespray's cert dir, then kubeadm's.
CLUSTER_CA = ("/etc/kubernetes/ssl/ca.crt", "/etc/kubernetes/pki/ca.crt")
# Neighbour states of a gateway that answered ARP, or has a static entry.
NEIGH_ALIVE = {"REACHABLE", "PERMANENT", "NOARP"}
NEIGH_STATES = NEIGH_ALIVE | {"STALE", "DELAY", "PROBE", "FAILED", "INCOMPLETE", "NONE"}
# apt, dpkg and unattended-upgrade hold a POSIX lock on these while they run.
APT_LOCKS = ("/var/lib/dpkg/lock-frontend", "/var/lib/dpkg/lock", "/var/lib/apt/lists/lock",
             "/var/cache/apt/archives/lock", "/run/unattended-upgrades.lock")
# struct flock on 64-bit Linux: l_type, l_whence, l_start, l_len, l_pid, padding.
FLOCK = "hhqqi4x"


def run(args, timeout=10, stderr=True):
    """(rc, output) of a command found on PATH, in the C locale; (None, '') when it isn't installed,
    (-1, error) when it could not run or timed out. stderr=False leaves warnings out of the output."""
    path = shutil.which(args[0])
    if not path:
        return None, ""
    try:
        res = subprocess.run([path] + list(args[1:]), stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT if stderr else subprocess.DEVNULL,
                             universal_newlines=True, timeout=timeout, env=dict(os.environ, LC_ALL="C"))
        return res.returncode, res.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)


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
    """The default route's first gateway (a multipath route lists one per nexthop). With BGP
    unnumbered the IPv4 default goes via an IPv6 link-local address: "via inet6 fe80::1 dev eth0"."""
    rc, out = run(["ip", "-o", "route", "show", "default"])
    route = next((line for line in (out or "").splitlines() if line.startswith("default")), "")
    via = re.search(r"\bvia (?:inet6 )?(\S+)", route)
    dev = re.search(r"\bdev (\S+)", route)
    gw = via.group(1) if via else None
    result = {"ip": gw, "dev": dev.group(1) if dev else None, "reachable": False, "via": None}
    if not gw:
        return result
    # A link-local gateway is only reachable through its interface.
    target = "%s%%%s" % (gw, result["dev"]) if gw.lower().startswith("fe80:") and result["dev"] else gw
    rc, _ = run(["ping", "-c", "2", "-W", "2", target], timeout=10)
    if rc == 0:
        result.update(reachable=True, via="icmp")
        return result
    for port in (22, 443):
        try:
            socket.create_connection((target, port), timeout=3).close()
        except ConnectionRefusedError:
            pass  # the gateway answered
        except OSError:
            continue
        result.update(reachable=True, via="tcp/%d" % port)
        return result
    # A gateway that drops pings and TCP still answers ARP. The ping and TCP tries took
    # long enough for the kernel to re-check an old entry, so only a confirmed one counts.
    rc, out = run(["ip", "neigh", "show", gw] + (["dev", result["dev"]] if result["dev"] else []))
    # e.g. "192.0.2.1 lladdr 16:18:2f:83:38:dc REACHABLE" or "... extern_learn NOARP proto zebra"
    words = next((line.split() for line in out.splitlines() if rc == 0 and line.split()[:1] == [gw]), [])
    if next((w for w in words if w in NEIGH_STATES), None) in NEIGH_ALIVE:
        result.update(reachable=True, via="arp")
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


def _answer(code):
    """What an HTTP status from the site or the proxy means for reaching the site."""
    if code == 407:
        return {"ok": False, "error": "proxy authentication required"}
    if code == 403:  # the site, or a proxy, answered but refused
        return {"ok": True, "refused": True}
    if code >= 500:
        return {"ok": False, "error": "HTTP %d (the proxy could not reach it)" % code}
    return {"ok": True}


def reach(urls, timeout=10):
    """HTTPS through the proxy from the environment. A 4xx answer still means the site
    was reached (registry.k8s.io answers 401); 407 and 5xx mean it wasn't: a proxy
    answers 502/504 itself when it can't reach the site. 403 is reached, but refused."""
    out = {}
    for url in urls:
        try:
            # Fixed https URLs from the spec (kubernetes_node_preflight_urls).
            with urllib.request.urlopen(url, timeout=timeout):  # nosec B310
                out[url] = {"ok": True}
        except urllib.error.HTTPError as exc:
            out[url] = _answer(exc.code)
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            reason = str(getattr(exc, "reason", exc))
            # A proxy that refuses the HTTPS tunnel: "Tunnel connection failed: 407 ..."
            tunnel = re.match(r"Tunnel connection failed: (403|407)\b", reason)
            out[url] = _answer(int(tunnel.group(1))) if tunnel else {"ok": False, "error": reason}
        except ValueError:
            out[url] = {"ok": False, "error": "not a URL, it needs https://"}
    return out


def addresses():
    """The node's own IP addresses. Kubespray binds the API server to one of them,
    the default-route address when the inventory sets no ip."""
    rc, out = run(["ip", "-o", "addr", "show"])
    return sorted(set(re.findall(r"\binet6?\s+([0-9A-Fa-f:.]+)/", out or "")))


def ca_hash(pem):
    """sha256 of the first certificate in a PEM text, or None."""
    match = re.search(r"-----BEGIN CERTIFICATE-----(.+?)-----END CERTIFICATE-----", pem, re.S)
    if not match:
        return None
    try:
        return hashlib.sha256(base64.b64decode("".join(match.group(1).split()), validate=True)).hexdigest()
    except ValueError:
        return None


def kubeconfig(conf):
    """(server, ca_hash) of a kubeconfig. The CA is its certificate-authority-data, or the file
    its certificate-authority names; None for each that is missing or unreadable."""
    text = read(conf)
    server = re.search(r"^\s*server:\s*(\S+)", text, re.M)
    data = re.search(r"^\s*certificate-authority-data:\s*(\S+)", text, re.M)
    path = re.search(r"^\s*certificate-authority:\s*(\S+)", text, re.M)
    try:
        # A relative certificate-authority is relative to the kubeconfig.
        pem = (base64.b64decode(data.group(1).strip("'\""), validate=True).decode("ascii", "replace") if data
               else read(os.path.join(os.path.dirname(conf), path.group(1).strip("'\""))) if path else "")
    except ValueError:
        pem = ""
    return (server.group(1).strip("'\"") if server else None), ca_hash(pem)


def kube_leftovers(bin_dirs):
    kubelet_server, kubelet_ca = kubeconfig("/etc/kubernetes/kubelet.conf")
    admin_server, admin_ca = kubeconfig("/etc/kubernetes/admin.conf")
    rc, out = run(["systemctl", "is-active", "kubelet"])
    manifests = "/etc/kubernetes/manifests"
    cni = "/etc/cni/net.d"
    return {
        "kubelet_active": out == "active",
        "kubelet_conf_server": kubelet_server,
        "admin_conf_server": admin_server,
        "kubelet_conf_ca": kubelet_ca,
        "admin_conf_ca": admin_ca,
        "cluster_ca": ca_hash(read(CLUSTER_CA[0]) or read(CLUSTER_CA[1])),
        "manifests": sorted(os.listdir(manifests)) if os.path.isdir(manifests) else [],
        "etcd_member": os.path.isdir("/var/lib/etcd/member"),
        # An etcd binary without its unit (a half-finished reset) makes Kubespray
        # treat etcd as installed and fail at "Stop etcd".
        "etcd_binary": next((d + "/etcd" for d in bin_dirs if os.path.exists(d + "/etcd")), None),
        "etcd_unit": any(os.path.exists(d + "/etcd.service")
                         for d in ("/etc/systemd/system", "/lib/systemd/system", "/usr/lib/systemd/system")),
        "cni_calico": sorted(f for f in os.listdir(cni) if "calico" in f) if os.path.isdir(cni) else [],
        "addresses": addresses(),
    }


def listeners(ports):
    """Owners of the listening TCP sockets on the given ports (needs root for names)."""
    rc, out = run(["ss", "-H", "-ltnp"])
    if rc != 0:
        raise RuntimeError("ss: %s; ports not checked" % (out or "not installed"))
    found = []
    for line in (out or "").splitlines():
        cols = line.split()
        if len(cols) < 4:
            continue
        port = cols[3].rsplit(":", 1)[-1]
        if not port.isdigit() or int(port) not in ports:
            continue
        proc = re.search(r'users:\(\("([^"]+)",pid=(\d+)', line)
        entry = {"port": int(port), "proc": proc.group(1) if proc else "?",
                 "pid": int(proc.group(2)) if proc else None}
        if entry not in found:  # one socket per address family
            found.append(entry)
    return found


def apt_lock_holders():
    """'<name> (pid N)' of each process holding an apt or dpkg lock, as the kernel reports it (F_GETLK). By
    lock, not by process name: Ubuntu's idle unattended-upgrade-shutdown helper is also named 'unattended-upgr',
    and holds none."""
    holders = set()
    for path in APT_LOCKS:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            query = struct.pack(FLOCK, fcntl.F_WRLCK, os.SEEK_SET, 0, 0, 0)
            kind, _, _, _, pid = struct.unpack(FLOCK, fcntl.fcntl(fd, fcntl.F_GETLK, query))
        except OSError:
            continue
        finally:
            os.close(fd)
        if kind != fcntl.F_UNLCK:
            holders.add("%s (pid %d)" % (read("/proc/%d/comm" % pid, "?"), pid) if pid > 0 else "an unknown process")
    return sorted(holders)


def package_manager():
    busy = apt_lock_holders()
    # What it lists on stdout, or its exit status; dpkg's warnings on stderr are not problems.
    rc, out = run(["dpkg", "--audit"], timeout=30, stderr=False)
    audit = None if rc is None else out or ("" if rc == 0 else "exit status %d" % rc)
    return {"dpkg_audit": audit, "busy": busy}


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
        ("kube", ("N10", "N11", "N19"), lambda: kube_leftovers(spec.get("bin_dirs") or ["/usr/local/bin"])),
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
