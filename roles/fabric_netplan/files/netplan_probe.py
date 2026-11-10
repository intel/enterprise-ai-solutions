# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Other netplan and NetworkManager configuration that also configures a fabric NIC.

Usage: netplan_probe.py --fabric-file NAME --old-file NAME NIC...
Prints one "nmprof=" or "netplandef=" line per finding.
"""
import argparse
import configparser
import fnmatch
import glob
import os

import yaml

NETPLAN_DIRS = ("lib/netplan", "etc/netplan", "run/netplan")


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def nic_facts(root, nics):
    """(MAC, driver) of each NIC, from sysfs."""
    facts = {}
    for n in nics:
        base = os.path.join(root, "sys/class/net", n)
        driver = os.path.join(base, "device/driver")
        facts[n] = (_read(os.path.join(base, "address")).lower(),
                    os.path.basename(os.path.realpath(driver)) if os.path.exists(driver) else "")
    return facts


def selects(match, nic, facts):
    """Whether a netplan match: selects nic. Like networkd's [Match], every key given must match."""
    mac, driver = facts.get(nic, ("", ""))
    drivers = match.get("driver") or []
    tests = []
    if match.get("name"):
        tests.append(fnmatch.fnmatchcase(nic, str(match["name"])))
    if match.get("macaddress"):
        tests.append(mac == str(match["macaddress"]).lower())
    if drivers:
        tests.append(any(fnmatch.fnmatchcase(driver, str(d))
                         for d in (drivers if isinstance(drivers, list) else [drivers])))
    return bool(tests) and all(tests)


def netplan_files(root, skip):
    """(path, network:) of each netplan file netplan reads, in its merge order, without the fabric files.

    A file shadows one of the same name in an earlier directory (run over etc over lib).
    """
    by_name = {os.path.basename(p): p for d in NETPLAN_DIRS for p in sorted(glob.glob(os.path.join(root, d, "*.yaml")))}
    out = []
    for name in sorted(by_name):
        if name in skip:
            continue
        try:
            with open(by_name[name]) as f:
                net = (yaml.safe_load(f) or {}).get("network") or {}
        except (OSError, ValueError, yaml.YAMLError, AttributeError):
            continue
        if isinstance(net, dict):
            out.append((by_name[name], net))
    return out


def scan(root, fabric_file, old_file, nics):
    facts = nic_facts(root, nics)
    files = netplan_files(root, {fabric_file, old_file})

    def entries():
        for path, net in files:
            eths = net.get("ethernets") or {}
            for dev, d in eths.items() if isinstance(eths, dict) else []:
                if isinstance(d, dict):
                    yield path, net, eths, dev, d

    # netplan merges the entries that share an ID across files, match: included,
    # so a match: in one file also selects the NIC for the keys another file sets.
    matches = {}
    for _, _, _, dev, d in entries():
        if isinstance(d.get("match"), dict):
            matches[dev] = {**matches.get(dev, {}), **d["match"]}
    # An ID named after a fabric NIC is the fabric file's own netdef, whatever match: it has.
    members = {dev: [n for n in nics if selects(m, n, facts)] for dev, m in matches.items() if dev not in nics}

    lines = []
    for path, net, eths, dev, d in entries():
        members.setdefault(dev, [n for n in nics if n == dev])
        if (d.get("renderer") or eths.get("renderer") or net.get("renderer")) == "NetworkManager":
            lines += ["nmprof=%s in %s (profile '%s')" % (n, path, (d.get("networkmanager") or {}).get("name", dev))
                      for n in members[dev]]
            continue
        keys = [k for k in ("addresses", "dhcp4", "dhcp6", "routes", "gateway4", "gateway6", "set-name") if d.get(k)]
        if dev in nics:
            # Merged into the fabric netdef. Its match: must still select this NIC, or netplan
            # moves the fabric configuration to another one; set-name to its own name is harmless.
            if d.get("match") and not selects(matches[dev], dev, facts):
                lines.append("netplandef=%s in %s ('%s' has a match: that selects a different NIC)" % (dev, path, dev))
            if d.get("set-name") == dev:
                keys.remove("set-name")
        if dev in matches and dev not in nics:
            if d.get("match"):
                # A separate netdef, not merged with the fabric file: networkd applies only the
                # first matching .network file by name, so the entry can take the NIC even if it sets nothing.
                what = "matches it" + (" and sets " + ", ".join(keys) if keys else "")
            elif keys:
                what = "sets " + ", ".join(keys)
            else:
                continue
        else:
            # Merged with the fabric file: lists add up, and on single values the file
            # that sorts later wins, so its dhcp4/dhcp6: false overrides an earlier file.
            if os.path.basename(path) < fabric_file:
                keys = [k for k in keys if k not in ("dhcp4", "dhcp6")]
            if not keys:
                continue
            what = "sets " + ", ".join(keys)
        lines += ["netplandef=%s in %s ('%s' %s)" % (n, path, dev, what) for n in members[dev]]
    for path, net in files:
        for kind in ("bonds", "bridges"):
            group = net.get(kind) or {}
            for dev, d in group.items() if isinstance(group, dict) else []:
                for ref in (d.get("interfaces") or []) if isinstance(d, dict) else []:
                    # interfaces: lists netplan IDs; an ID defined with match: selects other NICs.
                    for n in members.get(ref, [ref] if ref in nics else []):
                        lines.append("netplandef=%s in %s (member of %s '%s')" % (n, path, kind[:-1], dev))
    for path in sorted(glob.glob(os.path.join(root, "etc/NetworkManager/system-connections/*"))):
        c = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            c.read(path)
        except (configparser.Error, ValueError):
            continue
        match = {"name": c.get("connection", "interface-name", fallback=None),
                 "macaddress": c.get("ethernet", "mac-address", fallback=None)}
        name = c.get("connection", "id", fallback=os.path.basename(path))
        lines += ["nmprof=%s in %s (profile '%s')" % (n, path, name) for n in nics if selects(match, n, facts)]
    return lines


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--fabric-file", required=True)
    p.add_argument("--old-file", required=True)
    p.add_argument("--root", default="/")
    p.add_argument("nics", nargs="*")
    a = p.parse_args()
    for line in scan(a.root, a.fabric_file, a.old_file, a.nics):
        print(line)


if __name__ == "__main__":
    main()
