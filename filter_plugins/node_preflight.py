# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Node preflight rules: the probe's JSON from each node becomes rows, failures, warnings and a table.

FAIL stops the install, WARN is printed. The check IDs are what kubernetes_node_preflight_skip takes.
"""

import ipaddress
import json
import re
from urllib.parse import urlparse

GIB = 1 << 30

TITLES = {
    "N1": "Time sync", "N2": "Clock skew", "N3": "Disk for images", "N4": "Root disk", "N5": "etcd disk",
    "N6": "Gateway", "N7": "DNS", "N8": "Internet", "N9": "sudo", "N10": "Another cluster",
    "N11": "Leftovers", "N12": "Ports", "N13": "Package manager", "N14": "Firewall",
    "N15": "OS release", "N16": "Ceph disks", "N19": "Half-removed etcd",
}
# Our own components may hold the Kubernetes ports (left from an earlier attempt:
# N11, not N12). ss truncates process names to 15 characters.
KUBE_PROCS = {"kubelet", "kube-apiserver", "kube-controller", "kube-scheduler", "etcd", "kube-proxy",
              "bird", "bird6", "calico-node"}
LOOPBACK = {"localhost", "127.0.0.1", "::1"}
KUBECONFIGS = (("kubelet.conf", "kubelet_conf_server", "kubelet_conf_ca"),
               ("admin.conf", "admin_conf_server", "admin_conf_ca"))
RACK_SCALE_OS = {("ubuntu", "22.04"), ("ubuntu", "24.04")}
# sudo's harmless warning, and the errors that mean sudo did not let the probe run.
SUDO_WARNING = re.compile(r"sudo: unable to resolve host")
SUDO_ERROR = re.compile(r"password is required|terminal is required|not in the sudoers|incorrect (sudo )?password"
                        r"|missing sudo password|privilege escalation", re.I)
SUDO_HINT = "Allow passwordless sudo for the Ansible user, or run with --ask-become-pass"


def _gib(n):
    return "%.0f GiB" % (n / GIB)


def _host(url):
    try:
        return urlparse(url).hostname or url
    except ValueError:
        return url


def _canonical(host):
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return str(host).lower()


def _time_checks(data, ctx):
    sync = data["time"]
    rows = [("N1", "OK" if sync.get("synced") else "FAIL", "%s, %s" % (sync.get("source"), sync.get("detail")),
             "Enable time sync: sudo systemctl enable --now chrony (or systemd-timesyncd), "
             "then check chronyc tracking")]
    if ctx.get("local"):
        rows.append(("N2", "OK", "this is the deploy node", ""))
    elif data.get("controller_epoch") is None:
        rows.append(("N2", "WARN", "no deploy-node time to compare with", ""))
    else:
        skew = data["node_epoch"] - float(data["controller_epoch"])
        status = ("FAIL" if abs(skew) > ctx["max_skew_s"] else
                  "WARN" if abs(skew) > ctx["warn_skew_s"] else "OK")
        rows.append(("N2", status, "%+.1f s against the deploy node (includes SSH latency)" % skew,
                     "Point every node and the deploy node at the same NTP servers"))
    return rows


def _disk_checks(disk, ctx):
    images, root, etcd = disk["containerd"], disk["root"], disk.get("etcd")
    status = ("FAIL" if images["avail"] < ctx["min_free_gib"] * GIB or images["used_pct"] > ctx["max_used_pct"] else
              "WARN" if images["avail"] < ctx["warn_free_gib"] * GIB else "OK")
    rows = [("N3", status, "%s free, %s%% used on %s" % (_gib(images["avail"]), images["used_pct"], images["path"]),
             "Container images need space under /var/lib/containerd: free at least %d GiB there, "
             "or mount a larger disk" % ctx["min_free_gib"]),
            ("N4", "FAIL" if root["used_pct"] > ctx["max_used_pct"] else "OK",
             "%s%% used, %s free on /" % (root["used_pct"], _gib(root["avail"])), "Free space on /")]
    if etcd:
        rows.append(("N5", "WARN" if etcd["avail"] < ctx["etcd_warn_free_gib"] * GIB else "OK",
                     "%s free on %s" % (_gib(etcd["avail"]), etcd["path"]),
                     "etcd keeps its data under /var/lib/etcd: give it a fast disk with room to grow"))
    return rows


def _gateway_checks(gateway):
    if not gateway.get("ip"):
        if gateway.get("dev"):
            return [("N6", "OK", "default route on %s, no gateway" % gateway["dev"], "")]
        return [("N6", "FAIL", "no default route", "Add a default route on the management network")]
    return [("N6", "OK" if gateway.get("reachable") else "FAIL",
             "%s %s" % (gateway["ip"], "reachable (%s)" % gateway["via"] if gateway.get("reachable") else "unreachable"),
             "Check the default gateway on %s" % (gateway.get("dev") or "the management NIC"))]


def _dns_checks(dns, ctx):
    proxy = ctx.get("proxy_host") or ""
    bad = sorted(name for name, res in dns.items() if not res.get("ok"))
    if proxy and proxy in bad:
        status, evidence = "FAIL", "cannot resolve the proxy %s" % proxy
    elif bad:
        # The node needn't resolve these itself behind a proxy, or with a mirror (N8 skipped).
        offline = "N8" in (ctx.get("skip") or [])
        status, evidence = ("WARN" if proxy or offline else "FAIL"), "cannot resolve " + ", ".join(bad)
    else:
        status, evidence = "OK", "%d names resolve" % len(dns)
    return [("N7", status, evidence, "Check /etc/resolv.conf and the DNS servers on this node. Nodes that download "
             "from a mirror list it in kubernetes_node_preflight_dns_names")]


def _internet_checks(urls):
    bad = ["%s (%s)" % (_host(url), res.get("error")) for url, res in sorted(urls.items()) if not res.get("ok")]
    refused = [_host(url) for url, res in sorted(urls.items()) if res.get("refused")]
    if bad:
        status, evidence = "FAIL", "cannot reach " + ", ".join(bad)
    elif refused:
        status, evidence = "WARN", "reached but refused (403): " + ", ".join(refused)
    else:
        status, evidence = "OK", "reaches " + ", ".join(sorted(_host(u) for u in urls))
    return [("N8", status, evidence,
             "Kubespray downloads on every node: set http_proxy/https_proxy/no_proxy in global_config.yaml, "
             "or open HTTPS. Nodes that download from a mirror skip this check (kubernetes_node_preflight_skip: [N8])")]


def _sudo_checks(euid):
    root = euid == 0
    return [("N9", "OK" if root else "FAIL", "runs as root through sudo" if root else "euid %s after become" % euid,
             SUDO_HINT)]


def _other_cluster(kube, cp):
    """N10: (failures, warnings) for this node's kubeconfigs against this run's control plane."""
    fail, warn = [], []
    if kube.get("cluster_ca") in cp["cas"] and len(cp["cas"]) > 1:
        fail.append("this run's control-plane nodes have %d different cluster CAs" % len(cp["cas"]))
    for conf, server_key, ca_key in KUBECONFIGS:
        server, ca = kube.get(server_key), kube.get(ca_key)
        host = _canonical(_host(server)) if server else None
        if host is None:
            continue
        if host not in cp["by_ca"]:
            if host not in cp["hosts"]:
                fail.append("%s points at %s" % (conf, server))
        # Kubespray points kubeconfigs at 127.0.0.1, its local API proxy or its load balancer name,
        # the same in every Kubespray cluster: there the cluster CA tells.
        elif ca is None:
            fail.append("%s points at %s, left over from a cluster; its CA is missing or unreadable" % (conf, server))
        elif ca not in cp["cas"] and cp["unprobed"]:
            warn.append("cannot compare the CA of %s: %s not probed" % (conf, ", ".join(cp["unprobed"])))
        elif ca not in cp["cas"]:
            fail.append("%s points at %s with another cluster's CA" % (conf, server))
    return fail, warn


def _cluster_checks(kube, ports, etcd_node, cp):
    fail, warn = _other_cluster(kube, cp)
    rows = [("N10", "FAIL" if fail else "WARN" if warn else "OK", "; ".join(fail + warn) or "no other cluster's config",
             "This node still belongs to another cluster. Remove it from that cluster and clean it "
             "(kubeadm reset, then remove /etc/kubernetes, /var/lib/etcd and /etc/cni/net.d) before installing"
             if fail else "Fix the control-plane node's probe first; N10 then compares the cluster CA")]
    left = []
    if kube.get("kubelet_active"):
        left.append("kubelet running")
    if kube.get("manifests"):
        left.append("/etc/kubernetes/manifests: " + ", ".join(kube["manifests"]))
    if kube.get("etcd_member"):
        left.append("/var/lib/etcd/member")
    if kube.get("cni_calico"):
        left.append("/etc/cni/net.d: " + ", ".join(kube["cni_calico"]))
    if kube.get("etcd_binary") and kube.get("etcd_unit"):
        left.append("etcd installed")
    left += ["port %d held by %s" % (p["port"], p["proc"]) for p in ports or [] if p["proc"] in KUBE_PROCS]
    rows.append(("N11", "WARN" if left else "OK", "; ".join(left) or "none",
                 "Left from an earlier install. A re-run usually reuses it; if Kubespray fails on this node, "
                 "run teardown or clean the node first"))
    if etcd_node:
        half = kube.get("etcd_binary") and not kube.get("etcd_unit")
        rows.append(("N19", "FAIL" if half else "OK",
                     "%s without an etcd service" % kube["etcd_binary"] if half else "no half-removed etcd",
                     "Left by an unfinished reset: Kubespray takes it for an installed etcd and fails at "
                     "'Stop etcd'. Run the teardown (Kubespray reset) again, or remove etcd, etcdctl and etcdutl "
                     "from that directory and /etc/etcd.env on this node"))
    return rows


def _port_checks(ports):
    taken = ["port %d held by %s (pid %s)" % (p["port"], p["proc"], p["pid"]) for p in ports
             if p["proc"] not in KUBE_PROCS]
    return [("N12", "FAIL" if taken else "OK", "; ".join(taken) or "free",
             "Another program holds a port Kubernetes needs: stop it, or move it to another port")]


def _package_checks(pkg):
    if pkg.get("dpkg_audit") is None:
        return [("N13", "OK", "not a dpkg system", "")]
    if pkg["dpkg_audit"]:
        return [("N13", "FAIL", "dpkg --audit: " + pkg["dpkg_audit"].splitlines()[0],
                 "Repair the package state: sudo dpkg --configure -a && sudo apt-get -f install")]
    if pkg.get("busy"):
        return [("N13", "WARN", "running: " + ", ".join(pkg["busy"]),
                 "apt is busy (often unattended-upgrades); Kubespray retries, but it can time out")]
    return [("N13", "OK", "healthy", "")]


def _firewall_checks(firewall):
    active = [name for name in ("ufw", "firewalld") if firewall.get(name)]
    return [("N14", "WARN" if active else "OK", ("active: " + ", ".join(active)) if active else "none active",
             "Kubernetes needs its ports open between nodes (and 179 for BGP): open them, or disable the firewall")]


def _os_checks(release):
    ok = (release.get("id"), release.get("version_id")) in RACK_SCALE_OS
    return [("N15", "OK" if ok else "FAIL", release.get("pretty") or "unknown",
             "Rack-scale configures the fabric with netplan: use Ubuntu 22.04 or 24.04")]


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


def _checks(data, ctx, cp):
    """(id, status, evidence, hint) for one node's probe data. A probe the node skipped is absent."""
    rows = []
    if data.get("time"):
        rows += _time_checks(data, ctx)
    if data.get("disk"):
        rows += _disk_checks(data["disk"], ctx)
    if data.get("gateway"):
        rows += _gateway_checks(data["gateway"])
    if data.get("dns") is not None:
        rows += _dns_checks(data["dns"], ctx)
    if data.get("urls") is not None:
        rows += _internet_checks(data["urls"])
    rows += _sudo_checks(data.get("euid"))
    if data.get("kube") is not None:
        rows += _cluster_checks(data["kube"], data.get("ports"), data.get("etcd_node"), cp)
    if data.get("ports") is not None:
        rows += _port_checks(data["ports"])
    if data.get("pkg"):
        rows += _package_checks(data["pkg"])
    if data.get("firewall"):
        rows += _firewall_checks(data["firewall"])
    if ctx.get("rack_scale"):
        rows += _os_checks(data.get("os", {}))
    if ctx.get("ceph") and data.get("ceph"):
        rows += _ceph_checks(data["ceph"])
    rows += [("N0", "WARN", "probe error: " + err, "Report this; the other checks still ran")
             for err in data.get("errors", [])]
    return rows


def _probe_json(stdout):
    """The probe's JSON line, or None. With ssh -tt, sudo's warnings ("unable to resolve host") come first."""
    lines = [line for line in (stdout or "").splitlines() if line.strip().startswith("{")]
    return json.loads(lines[-1]) if lines else None


def _probe_failed(res):
    """The probe printed no JSON: sudo wanted a password, SSH failed, or python3 is missing.

    When the script ran (rc is set) its output says why; Ansible's msg is then only "non-zero return code".
    An unreachable node (SSH, or a become timeout) has only msg.
    """
    ran = res.get("rc") is not None
    sources = (res.get("stdout"), res.get("stderr"), res.get("msg")) if ran else (res.get("msg"),)
    lines = [line.strip() for s in sources if s for line in s.splitlines()
             if line.strip() and not SUDO_WARNING.match(line.strip())]
    msg = lines[0] if lines else "no output"
    sudo = next((line for line in lines if SUDO_ERROR.search(line)), None)
    if sudo:
        return [("N9", "FAIL", sudo[:300], SUDO_HINT)]
    return [("N0", "FAIL", msg[:300],
             "The probe did not run or did not finish on this node: it needs python3, and SSH and sudo that answer")]


def node_preflight_skip(value):
    """kubernetes_node_preflight_skip as check IDs: a list or a comma-separated string, in any case."""
    items = value.split(",") if isinstance(value, str) else value or []
    ids = sorted({str(v).strip().upper() for v in items} - {""})
    unknown = [i for i in ids if i not in TITLES]
    if unknown:
        raise ValueError("kubernetes_node_preflight_skip: no check %s; the checks are %s"
                         % (", ".join(unknown), ", ".join(TITLES)))
    return ids


def node_preflight_evaluate(results, ctx):
    """Rows for every node and check, the failures and warnings, and a printable table."""
    skip = set(ctx.get("skip") or [])
    parsed = []
    for res in results or []:
        try:
            parsed.append((res, _probe_json(res.get("stdout"))))
        except ValueError:
            parsed.append((res, None))
    # This run's control plane: the API servers it can serve (the inventory's names and addresses,
    # and every address its nodes report: Kubespray may bind the default-route one) and its CAs.
    control_plane = set(ctx.get("control_plane") or [])
    cp_kube = [data.get("kube") or {} for res, data in parsed if data and res.get("item") in control_plane]
    cp = {"hosts": {_canonical(h) for h in ctx.get("api_hosts") or []} | {
              _canonical(a) for kube in cp_kube for a in kube.get("addresses", [])},
          "cas": {kube.get("cluster_ca") for kube in cp_kube} - {None},
          "by_ca": LOOPBACK | ({_canonical(ctx["api_lb"])} if ctx.get("api_lb") else set()),
          "unprobed": [res.get("item") for res, data in parsed if data is None and res.get("item") in control_plane]}
    rows = []
    for res, data in parsed:
        checks = _checks(data, ctx, cp) if data is not None else _probe_failed(res)
        # A check the node did not probe because it is skipped is still listed, as SKIP.
        probed = {c[0] for c in checks}
        checks += [(cid, "SKIP", "in kubernetes_node_preflight_skip", "") for cid in TITLES
                   if cid in skip and cid not in probed]
        for cid, status, evidence, hint in checks:
            rows.append({"node": res.get("item"), "id": cid, "check": TITLES.get(cid, "Probe"),
                         "status": "SKIP" if cid in skip else status, "evidence": evidence, "hint": hint})

    failures = [r for r in rows if r["status"] == "FAIL"]
    warnings = [r for r in rows if r["status"] == "WARN"]
    table = []
    for node in [res.get("item") for res in results or []]:
        mine = [r for r in rows if r["node"] == node]
        counts = {s: sum(1 for r in mine if r["status"] == s) for s in ("OK", "WARN", "FAIL", "SKIP")}
        table.append("%s: %d ok, %d warn, %d fail%s" % (node, counts["OK"], counts["WARN"], counts["FAIL"],
                                                         ", %d skipped" % counts["SKIP"] if counts["SKIP"] else ""))
        table += ["  %s %-4s %-15s %s" % (r["id"], r["status"], r["check"], r["evidence"])
                  for r in mine if r["status"] in ("FAIL", "WARN")]
    return {"rows": rows, "failures": failures, "warnings": warnings, "table": table}


class FilterModule:
    def filters(self):
        return {"node_preflight_evaluate": node_preflight_evaluate, "node_preflight_skip": node_preflight_skip}
