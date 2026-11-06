# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin: the pass/fail rules of the node preflight.

roles/kubernetes/files/node_preflight_probe.py runs on every node and reports
what it found; this turns those reports into one row per node and check.

Usage in roles/kubernetes/tasks/node_preflight.yaml:
    {{ _np_probe.results | node_preflight_evaluate(ctx) }}

Statuses: FAIL stops the install, WARN is printed, OK and SKIP are counted,
INFO is the node summary. Check IDs (N1-N19) are what
kubernetes_node_preflight_skip takes.
"""

import json
import re
from urllib.parse import urlparse

GIB = 1 << 30

TITLES = {
    "N1": "Time sync", "N2": "Clock skew", "N3": "Disk for images", "N4": "Root disk", "N5": "etcd disk",
    "N6": "Gateway", "N7": "DNS", "N8": "Internet", "N9": "sudo", "N10": "Another cluster",
    "N11": "Leftovers", "N12": "Ports", "N13": "Package manager", "N14": "Firewall",
    "N15": "OS release", "N16": "Ceph disks", "N17": "Xeon tuning", "N18": "Node",
    "N19": "Half-removed etcd",
}
# Who may hold the Kubernetes ports: our own components, left over from an
# earlier attempt (reported by N11, not N12). ss truncates names to 15 chars.
KUBE_PROCS = {"kubelet", "kube-apiserver", "kube-controller", "kube-scheduler", "etcd", "kube-proxy",
              "bird", "bird6", "calico-node"}
RACK_SCALE_OS = {("ubuntu", "22.04"), ("ubuntu", "24.04")}


def _gib(n):
    return "%.0f GiB" % (n / GIB)


def _host(url):
    try:
        return urlparse(url).hostname or url
    except ValueError:
        return url


def _checks(data, ctx):
    """(id, status, evidence, hint) for one node's probe data."""
    out = []

    t = data.get("time")
    if t:
        out.append(("N1", "OK" if t.get("synced") else "FAIL", "%s, %s" % (t.get("source"), t.get("detail")),
                    "Enable time sync: sudo systemctl enable --now chrony (or systemd-timesyncd), "
                    "then check chronyc tracking"))
        if ctx.get("local"):
            out.append(("N2", "OK", "this is the deploy node", ""))
        elif data.get("controller_epoch") is None:
            out.append(("N2", "WARN", "no deploy-node time to compare with", ""))
        else:
            skew = data["node_epoch"] - float(data["controller_epoch"])
            status = ("FAIL" if abs(skew) > ctx["max_skew_s"] else
                      "WARN" if abs(skew) > ctx["warn_skew_s"] else "OK")
            out.append(("N2", status, "%+.1f s against the deploy node (includes SSH latency)" % skew,
                        "Point every node and the deploy node at the same NTP servers"))

    d = data.get("disk")
    if d:
        c = d["containerd"]
        status = ("FAIL" if c["avail"] < ctx["min_free_gib"] * GIB or c["used_pct"] > ctx["max_used_pct"] else
                  "WARN" if c["avail"] < ctx["warn_free_gib"] * GIB else "OK")
        out.append(("N3", status, "%s free, %s%% used on %s" % (_gib(c["avail"]), c["used_pct"], c["path"]),
                    "Container images need space under /var/lib/containerd: free at least %d GiB there, "
                    "or mount a larger disk" % ctx["min_free_gib"]))
        r = d["root"]
        out.append(("N4", "FAIL" if r["used_pct"] > ctx["max_used_pct"] else "OK",
                    "%s%% used, %s free on /" % (r["used_pct"], _gib(r["avail"])), "Free space on /"))
        e = d.get("etcd")
        if e:
            out.append(("N5", "WARN" if e["avail"] < ctx["etcd_warn_free_gib"] * GIB else "OK",
                        "%s free on %s" % (_gib(e["avail"]), e["path"]),
                        "etcd keeps its data under /var/lib/etcd: give it a fast disk with room to grow"))

    g = data.get("gateway")
    if g:
        if not g.get("ip"):
            out.append(("N6", "FAIL", "no default route", "Add a default route on the management network"))
        else:
            out.append(("N6", "OK" if g.get("reachable") else "FAIL",
                        "%s %s" % (g["ip"], "reachable (%s)" % g["via"] if g.get("reachable") else "unreachable"),
                        "Check the default gateway on %s" % (g.get("dev") or "the management NIC")))

    dns = data.get("dns")
    if dns is not None:
        proxy = ctx.get("proxy_host") or ""
        bad = sorted(n for n, r in dns.items() if not r.get("ok"))
        if proxy and proxy in bad:
            status, evidence = "FAIL", "cannot resolve the proxy %s" % proxy
        elif bad:
            # Behind a proxy the proxy resolves external names; without one the node must.
            status, evidence = ("WARN" if proxy else "FAIL"), "cannot resolve " + ", ".join(bad)
        else:
            status, evidence = "OK", "%d names resolve" % len(dns)
        out.append(("N7", status, evidence, "Check /etc/resolv.conf and the DNS servers on this node"))

    urls = data.get("urls")
    if urls is not None:
        bad = ["%s (%s)" % (_host(u), r.get("error") or r.get("status")) for u, r in sorted(urls.items())
               if not r.get("ok")]
        out.append(("N8", "FAIL" if bad else "OK",
                    ("cannot reach " + ", ".join(bad)) if bad else "reaches " + ", ".join(sorted(_host(u) for u in urls)),
                    "Kubespray downloads on every node: set http_proxy/https_proxy/no_proxy in global_config.yaml, "
                    "or open HTTPS. A site with its own mirror skips this check (kubernetes_node_preflight_skip: [N8])"))

    out.append(("N9", "OK" if data.get("euid") == 0 else "FAIL", "runs as root through sudo"
                if data.get("euid") == 0 else "euid %s after become" % data.get("euid"),
                "Allow passwordless sudo for the Ansible user, or run with --ask-become-pass"))

    k = data.get("kube")
    ports = data.get("ports")
    if k is not None:
        foreign = []
        for key, conf in (("kubelet_conf_server", "kubelet.conf"), ("admin_conf_server", "admin.conf")):
            if k.get(key) and _host(k[key]) not in ctx.get("api_hosts", []):
                foreign.append("%s points at %s" % (conf, k[key]))
        out.append(("N10", "FAIL" if foreign else "OK", "; ".join(foreign) or "no other cluster's config",
                    "This node still belongs to another cluster. Remove it from that cluster and clean it "
                    "(kubeadm reset, then remove /etc/kubernetes, /var/lib/etcd and /etc/cni/net.d) before installing"))
        left = []
        if k.get("kubelet_active"):
            left.append("kubelet running")
        if k.get("manifests"):
            left.append("/etc/kubernetes/manifests: " + ", ".join(k["manifests"]))
        if k.get("etcd_member"):
            left.append("/var/lib/etcd/member")
        if k.get("cni_calico"):
            left.append("/etc/cni/net.d: " + ", ".join(k["cni_calico"]))
        if k.get("etcd_binary") and k.get("etcd_unit"):
            left.append("etcd installed")
        left += ["port %d held by %s" % (p["port"], p["proc"]) for p in ports or [] if p["proc"] in KUBE_PROCS]
        out.append(("N11", "WARN" if left else "OK", "; ".join(left) or "none",
                    "Left from an earlier install. A re-run usually reuses it; if Kubespray fails on this node, "
                    "run teardown or clean the node first"))
    if k is not None and data.get("etcd_node"):
        half = k.get("etcd_binary") and not k.get("etcd_unit")
        out.append(("N19", "FAIL" if half else "OK",
                    "/usr/local/bin/etcd without an etcd service" if half else "no half-removed etcd",
                    "Left by an unfinished reset: Kubespray takes it for an installed etcd and fails at "
                    "'Stop etcd'. Run the teardown (Kubespray reset) again, or remove /usr/local/bin/etcd, "
                    "etcdctl, etcdutl and /etc/etcd.env on this node"))
    if ports is not None:
        taken = ["port %d held by %s (pid %s)" % (p["port"], p["proc"], p["pid"]) for p in ports
                 if p["proc"] not in KUBE_PROCS]
        out.append(("N12", "FAIL" if taken else "OK", "; ".join(taken) or "free",
                    "Another program holds a port Kubernetes needs: stop it, or move it to another port"))

    pkg = data.get("pkg")
    if pkg:
        if pkg.get("dpkg_audit") is None:
            out.append(("N13", "OK", "not a dpkg system", ""))
        elif pkg["dpkg_audit"]:
            out.append(("N13", "FAIL", "dpkg --audit: " + pkg["dpkg_audit"].splitlines()[0],
                        "Repair the package state: sudo dpkg --configure -a && sudo apt-get -f install"))
        elif pkg.get("busy"):
            out.append(("N13", "WARN", "running: " + ", ".join(pkg["busy"]),
                        "apt is busy (often unattended-upgrades); Kubespray retries, but it can time out"))
        else:
            out.append(("N13", "OK", "healthy", ""))

    fw = data.get("firewall")
    if fw:
        active = [n for n in ("ufw", "firewalld") if fw.get(n)]
        out.append(("N14", "WARN" if active else "OK", ("active: " + ", ".join(active)) if active else "none active",
                    "Kubernetes needs its ports open between nodes (and 179 for BGP): open them, or disable the firewall"))

    if ctx.get("rack_scale"):
        o = data.get("os", {})
        ok = (o.get("id"), o.get("version_id")) in RACK_SCALE_OS
        out.append(("N15", "OK" if ok else "FAIL", o.get("pretty") or "unknown",
                    "Rack-scale configures the fabric with netplan: use Ubuntu 22.04 or 24.04"))

    ceph = data.get("ceph")
    if ctx.get("ceph") and ceph:
        problems = []
        for dev, info in sorted(ceph.items()):
            why = []
            if not info.get("exists"):
                why.append("not found")
            else:
                if info.get("error"):
                    why.append(info["error"])
                if info.get("type") and info["type"] != "disk":
                    why.append("is a %s, not a whole disk" % info["type"])
                if info.get("mountpoints"):
                    why.append("mounted at " + ", ".join(info["mountpoints"]))
                if info.get("children"):
                    why.append("has partitions " + ", ".join(info["children"]))
                if info.get("fstype"):
                    why.append("has a %s signature" % info["fstype"])
                if info.get("bluestore_labels_gib"):
                    why.append("old Ceph BlueStore label at %s GiB" %
                               ", ".join(str(o) for o in info["bluestore_labels_gib"]))
            if why:
                problems.append("%s %s" % (dev, "; ".join(why)))
        out.append(("N16", "FAIL" if problems else "OK", " | ".join(problems) or
                    "%d disk(s) ready: %s" % (len(ceph), ", ".join(sorted(ceph))),
                    "Rook skips a disk that is mounted, partitioned or carries a signature, which leaves 0 OSDs: "
                    "check the device names in rack-profile.yaml / rook_ceph_devices, and wipe a former OSD disk "
                    "(docs/quickstart/prerequisites.md, Reusing a disk that held an OSD). Teardown keeps OSD data "
                    "unless rook_ceph_teardown_wipe_devices is true"))

    c = data.get("cpu")
    if c:
        tuning = []
        if c.get("governor") and c["governor"] != "performance":
            tuning.append("CPU governor %s" % c["governor"])
        if c.get("thp") == "always":
            tuning.append("transparent huge pages always")
        out.append(("N17", "WARN" if tuning else "OK", "; ".join(tuning) or "governor %s, THP %s" %
                    (c.get("governor") or "n/a", c.get("thp") or "n/a"),
                    "For CPU inference latency: cpupower frequency-set -g performance; "
                    "echo madvise > /sys/kernel/mm/transparent_hugepage/enabled"))
        o = data.get("os", {})
        out.append(("N18", "INFO", "%s, kernel %s, %s, %d socket(s), %s CPUs, %d NUMA node(s), %s RAM, AMX %s" % (
            o.get("pretty") or "?", data.get("kernel", "?"), c.get("model", "?"), c.get("sockets", 0),
            c.get("cpus", "?"), c.get("numa_nodes", 0), _gib(c.get("mem_bytes", 0)),
            "yes" if c.get("amx") else "no"), ""))

    for err in data.get("errors", []):
        out.append(("N0", "WARN", "probe error: " + err, "Report this; the other checks still ran"))
    return out


def node_preflight_evaluate(results, ctx):
    """Rows for every node and check, the failures and warnings, and a printable table."""
    skip = set(ctx.get("skip") or [])
    rows = []
    for res in results or []:
        node = res.get("item")
        if res.get("unreachable"):
            checks = [("N0", "FAIL", "unreachable: " + str(res.get("msg", "")), "Check SSH to this node")]
        else:
            try:
                data = json.loads(res.get("stdout") or "")
            except ValueError:
                data = None
            if data is None:
                msg = str(res.get("msg") or res.get("stderr") or res.get("stdout") or "no output")
                sudo = bool(re.search(r"sudo|password|become", msg, re.I))
                checks = [("N9" if sudo else "N0", "FAIL", msg.splitlines()[0][:300],
                           "Allow passwordless sudo for the Ansible user, or run with --ask-become-pass"
                           if sudo else "The probe could not run on this node (python3 needed)")]
            else:
                checks = _checks(data, ctx)
        for cid, status, evidence, hint in checks:
            if cid in skip and status != "INFO":
                status = "SKIP"
            rows.append({"node": node, "id": cid, "check": TITLES.get(cid, "Probe"), "status": status,
                         "evidence": evidence, "hint": hint})

    failures = [r for r in rows if r["status"] == "FAIL"]
    warnings = [r for r in rows if r["status"] == "WARN"]
    table = []
    for node in [res.get("item") for res in results or []]:
        mine = [r for r in rows if r["node"] == node]
        counts = {s: sum(1 for r in mine if r["status"] == s) for s in ("OK", "WARN", "FAIL", "SKIP")}
        table.append("%s: %d ok, %d warn, %d fail%s" % (node, counts["OK"], counts["WARN"], counts["FAIL"],
                                                         ", %d skipped" % counts["SKIP"] if counts["SKIP"] else ""))
        table += ["  %s %-4s %-15s %s" % (r["id"], r["status"], r["check"], r["evidence"])
                  for r in mine if r["status"] in ("FAIL", "WARN", "INFO")]
    return {"rows": rows, "failures": failures, "warnings": warnings, "table": table}


class FilterModule:
    def filters(self):
        return {"node_preflight_evaluate": node_preflight_evaluate}
