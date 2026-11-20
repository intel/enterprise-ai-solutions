#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# add-node/remove-node on a rack-scale env: the nodes the edited
# rack-profile.yaml declares, and what the edit changes for the nodes already
# in the inventory. toolkit_manager.sh runs it on the controller before
# anything is written, so --dry-run sees the same result.
#
# Usage: rack_scale_nodes.py <rack-profile.yaml> <env inventory dir>
# Prints tab-separated lines:
#   rack     <topology>
#   node     <name> <groups, comma-separated> <ansible_host> <ansible_user> <ssh key>
#   changed  <what the edit changes for a node in both the inventory and the profile>
# Exit 2, with the reasons on stderr, when the profile or the inventory does not parse.

import pathlib
import sys

import yaml

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "filter_plugins"))
import rack_profile_parser  # noqa: E402

# Generated once per rack, the same for every node: an edit that changes them
# changes the nodes that stay too.
RACK_FILES = (("Calico settings", "calico_yml", "metadata/k8s-net-calico.yml"),
              ("Ceph settings", "ceph_yml", "metadata/vars/ceph-storage.yml"))


def hosts(inventory):
    return (inventory.get("all") or {}).get("hosts") or {}


def host_groups(inventory):
    """{host: the groups that list it}"""
    groups = {}
    for group, body in ((inventory.get("all") or {}).get("children") or {}).items():
        for host in (body or {}).get("hosts") or {}:
            groups.setdefault(host, set()).add(group)
    return groups


def node_files(inventory, files):
    """{host: {path: content}}: a netplan file belongs to the host it is named
    after, a BGPPeer to its spec.node."""
    netplan = {"netplan/%s.yaml" % (v or {}).get("node_hostname", h): h for h, v in hosts(inventory).items()}
    owned = {}
    for path, content in files.items():
        owner = netplan.get(path) or ((yaml.safe_load(content) or {}).get("spec") or {}).get("node")
        owned.setdefault(owner, {})[path] = content
    return owned


def read_files(inventory_dir, subdir):
    return {str(p.relative_to(inventory_dir)): p.read_text()
            for p in sorted((inventory_dir / subdir).glob("*.yaml"))}


def changes(parsed, new, inventory_dir):
    """What the parsed profile (new: its inventory) changes for the hosts the inventory already has."""
    path = inventory_dir / "hosts.yaml"
    old = (yaml.safe_load(path.read_text()) if path.is_file() else None) or {}
    old_hosts, new_hosts = hosts(old), hosts(new)
    old_groups, new_groups = host_groups(old), host_groups(new)
    old_files = node_files(old, {**read_files(inventory_dir, "netplan"),
                                 **read_files(inventory_dir, "metadata/bgp-peers")})
    new_files = node_files(new, {**parsed["netplan_files"], **parsed["bgp_peer_files"]})
    out = []
    for host in (h for h in new_hosts if h in old_hosts):
        was, now = old_hosts[host] or {}, new_hosts[host] or {}
        keys = sorted(k for k in set(was) | set(now) if was.get(k) != now.get(k))
        if keys:
            out.append("%s: %s" % (host, ", ".join(keys)))
        if old_groups.get(host, set()) != new_groups.get(host, set()):
            out.append("%s: groups %s -> %s" % (host, ",".join(sorted(old_groups.get(host, ()))),
                                                ",".join(sorted(new_groups.get(host, ())))))
        if old_files.get(host, {}) != new_files.get(host, {}):
            out.append("%s: netplan or BGP peer files" % host)
    for what, key, rel in RACK_FILES:
        if (inventory_dir / rel).is_file() and (inventory_dir / rel).read_text() != parsed[key]:
            out.append(what)
    return out


def main(argv):
    if len(argv) != 3:
        sys.exit("usage: rack_scale_nodes.py <rack-profile.yaml> <env inventory dir>")
    try:
        parsed = rack_profile_parser.rack_profile_parse(yaml.safe_load(pathlib.Path(argv[1]).read_text()) or {})
        if parsed["errors"]:
            print("\n".join(parsed["errors"]), file=sys.stderr)
            return 2
        inventory = yaml.safe_load(parsed["hosts_yaml"])
        changed = changes(parsed, inventory, pathlib.Path(argv[2]))
    except (OSError, yaml.YAMLError) as exc:
        print(exc, file=sys.stderr)
        return 2
    if not hosts(inventory):
        print("%s declares no nodes" % argv[1], file=sys.stderr)
        return 2
    groups = host_groups(inventory)
    print("rack\t%s" % parsed["summary"]["topology"])
    for name, v in hosts(inventory).items():
        print("\t".join(["node", name, ",".join(sorted(groups.get(name, ()))), str(v.get("ansible_host", "")),
                         str(v.get("ansible_user", "")), str(v.get("ansible_ssh_private_key_file", ""))]))
    for change in changed:
        print("changed\t%s" % change)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
