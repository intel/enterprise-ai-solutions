# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — parses rack-profile.yaml and generates fabric artifacts.

Registered as Jinja filters via FilterModule (bottom). Auto-loaded from
filter_plugins/ dir configured in ansible.cfg.

Usage in fabric_inventory role:
    {{ rack_profile | rack_profile_parse }}
    {{ rack_profile | rack_profile_validate }}

Ported from core/lib/rack-profile-parser.sh (the embedded Python section).
Every validation, generator, and edge case is preserved.
"""

import ipaddress
import os

import yaml


def _to_native(obj):
    """Deep-convert Ansible tagged types to plain Python types.

    When rack-profile.yaml is loaded via include_vars, Ansible wraps every
    string in AnsibleTaggedStr (and dicts/lists in similar wrappers).  If
    these reach yaml.dump() the output contains !!python/object tags that
    break netplan and other consumers.  This function strips them.
    """
    if isinstance(obj, dict):
        return {_to_native(k): _to_native(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_native(v) for v in obj]
    if isinstance(obj, str):
        return str(obj)
    if isinstance(obj, bool):
        return bool(obj)
    if isinstance(obj, int):
        return int(obj)
    if isinstance(obj, float):
        return float(obj)
    # Fall back: force to string for any other Ansible wrapper type
    try:
        return str(obj)
    except (TypeError, ValueError):
        return obj


# ── Schema constants ─────────────────────────────────────────────────────────

VALID_PROFILE_KEYS = {
    "node_family", "sku", "accelerator", "gpu_count",
}
VALID_NODE_KEYS = {
    "hostname", "mgmt_ip", "local", "roles", "fabric_nics", "fabric_router",
    "ceph_fabric_nic", "ansible_user", "ansible_ssh_private_key_file",
    "node_family", "sku", "accelerator", "gpu_count", "rdma_nics",
    "profile",
    "_rack",        # meta key injected by the parser
    "mgmt_nic",     # legacy / deprecated (warned, not errored)
}
VALID_FABRIC_NIC_KEYS = {
    "nic", "fabric_ip", "prefix", "leaf", "peer",
}
KNOWN_CPU_FAMILIES = {"cpu-gnr-ap", "cpu-gnr-sp", "cpu-cwf", "cpu-spr"}
VALID_ACCELERATOR_VENDORS = {"nvidia", "intel", "amd"}
KNOWN_ROLES = {
    "kube_control_plane", "kube_node", "etcd", "storage",
    "gnr_ap", "gnr_sp", "cwf", "spr",
}


# ── Internal helpers ─────────────────────────────────────────────────────────

def _check_keys(block, valid, path, errors):
    for k in block:
        if k not in valid:
            errors.append(f"unknown field '{k}' in {path} — typo?")


def _safe_ip_address(val, context, errors):
    """Parse an IP address, returning None and appending an error on failure."""
    try:
        return ipaddress.ip_address(val)
    except (ValueError, TypeError):
        errors.append(f"{context}: '{val}' is not a valid IP address")
        return None


def _safe_ip_network(val, context, errors, strict=False):
    """Parse an IP network, returning None and appending an error on failure."""
    try:
        return ipaddress.ip_network(val, strict=strict)
    except (ValueError, TypeError):
        errors.append(f"{context}: '{val}' is not a valid CIDR network")
        return None


VALID_LEAF_KEYS = {"name", "asn", "peer_ip", "fabric_subnet", "model",
                   "switch_name", "switch_mgmt_ip"}


def _nic_prefix(fn, fabric_prefix):
    """On-link prefix length for one fabric NIC — per-NIC override, else site default."""
    return fn.get("prefix", fabric_prefix)


def _node_ceph_nic(node, ceph_nic):
    """Ceph fabric NIC for one node — per-node override, else site-wide default."""
    return node.get("ceph_fabric_nic", ceph_nic)


def _node_primary_nic(node):
    fnics = node.get("fabric_nics", [])
    return fnics[0] if fnics else None


def _ecmp_nodes(all_nodes, calico_mode, leaf_by_name):
    if calico_mode != "bgp":
        return []
    result = []
    for name, cfg in sorted(all_nodes.items()):
        routed = 0
        for fn in cfg.get("fabric_nics", []):
            leaf = leaf_by_name.get(fn.get("leaf"))
            if leaf and leaf.get("peer_ip", "TBD") != "TBD":
                routed += 1
        if routed >= 2:
            result.append(name)
    return result


def _fabric_supernets(all_nodes, fabric_prefix):
    nets = []
    for _, node in sorted(all_nodes.items()):
        for fn in node.get("fabric_nics", []):
            if not fn.get("fabric_ip"):
                continue
            net = ipaddress.ip_network(
                f"{fn['fabric_ip']}/{_nic_prefix(fn, fabric_prefix)}", strict=False)
            if net not in nets:
                nets.append(net)
    merged = [n for n in nets if not any(n != o and n.subnet_of(o) for o in nets)]
    return [str(n) for n in sorted(merged, key=lambda n: (n.network_address, n.prefixlen))]


def _fabric_addressing_conflicts(all_nodes, fabric_prefix):
    conflicts = []
    for name, node in sorted(all_nodes.items()):
        fnics = [fn for fn in node.get("fabric_nics", []) if fn.get("fabric_ip")]
        if len(fnics) < 2:
            continue
        nets = {}
        for fn in fnics:
            net = ipaddress.ip_network(
                f"{fn['fabric_ip']}/{_nic_prefix(fn, fabric_prefix)}", strict=False)
            nets.setdefault(net, []).append(fn)
        for net, sharing in sorted(nets.items(), key=lambda kv: str(kv[0])):
            if len(sharing) > 1:
                addrs = ", ".join(
                    f"{fn['nic']}={fn['fabric_ip']}/{_nic_prefix(fn, fabric_prefix)}"
                    for fn in sharing)
                conflicts.append((
                    name,
                    (f"{len(sharing)} of its own fabric NICs share the on-link prefix "
                     f"{net} ({addrs}) — the kernel cannot deterministically choose "
                     f"which NIC serves it")))
        primary = _node_primary_nic(node)
        if primary and primary.get("fabric_ip"):
            paddr = ipaddress.ip_address(primary["fabric_ip"])
            for fn in fnics:
                if fn is primary:
                    continue
                net = ipaddress.ip_network(
                    f"{fn['fabric_ip']}/{_nic_prefix(fn, fabric_prefix)}", strict=False)
                pnet = ipaddress.ip_network(
                    f"{primary['fabric_ip']}/{_nic_prefix(primary, fabric_prefix)}",
                    strict=False)
                if paddr in net and net != pnet:
                    conflicts.append((
                        name,
                        (f"primary {primary['nic']}={primary['fabric_ip']} falls inside "
                         f"the prefix {net} of secondary {fn['nic']}={fn['fabric_ip']} — "
                         f"the address published as ip/access_ip would be routed out the "
                         f"wrong NIC")))
    return conflicts


def _unusable_fabric_addresses(all_nodes, fabric_prefix):
    bad = []
    for name, node in sorted(all_nodes.items()):
        for fn in node.get("fabric_nics", []):
            if not fn.get("fabric_ip"):
                continue
            pfx = _nic_prefix(fn, fabric_prefix)
            if pfx >= 31:
                continue
            iface = ipaddress.ip_interface(f"{fn['fabric_ip']}/{pfx}")
            net = iface.network
            hosts = list(net.hosts())
            usable = f"{hosts[0]}-{hosts[-1]}" if hosts else "none"
            if iface.ip == net.network_address:
                bad.append((
                    name,
                    (f"NIC '{fn.get('nic')}' fabric_ip {iface.ip}/{pfx} is the "
                     f"NETWORK address of {net} — not assignable. "
                     f"Usable in this block: {usable}")))
            elif iface.ip == net.broadcast_address:
                bad.append((
                    name,
                    (f"NIC '{fn.get('nic')}' fabric_ip {iface.ip}/{pfx} is the "
                     f"BROADCAST address of {net} — not assignable. "
                     f"Usable in this block: {usable}")))
    return bad


def _star_cable_faults(all_nodes, fabric_prefix):
    faults = []
    for name, node in sorted(all_nodes.items()):
        for fn in node.get("fabric_nics", []):
            peer = fn.get("peer")
            if not peer or not fn.get("fabric_ip"):
                continue
            peer_cfg = all_nodes.get(peer)
            if peer_cfg is None:
                continue
            my_net = ipaddress.ip_network(
                f"{fn['fabric_ip']}/{_nic_prefix(fn, fabric_prefix)}", strict=False)
            back = [pfn for pfn in peer_cfg.get("fabric_nics", [])
                    if pfn.get("fabric_ip")
                    and ipaddress.ip_address(pfn["fabric_ip"]) in my_net]
            if not back:
                peer_addrs = ", ".join(
                    f"{p.get('nic')}={p.get('fabric_ip')}/{_nic_prefix(p, fabric_prefix)}"
                    for p in peer_cfg.get("fabric_nics", [])
                    if p.get("fabric_ip")) or "none"
                faults.append((
                    name,
                    (f"NIC '{fn.get('nic')}' {fn['fabric_ip']}/{_nic_prefix(fn, fabric_prefix)} "
                     f"declares peer '{peer}', but no NIC on '{peer}' is in {my_net}. "
                     f"'{peer}' has: {peer_addrs}. Both ends of a cable must share one "
                     f"/{_nic_prefix(fn, fabric_prefix)} or the link is not on-link and "
                     f"no route is generated")))
    return faults


def _cluster_cidr_faults(pod_cidr, service_cidr, all_nodes, fabric_prefix):
    faults = []
    parsed = {}
    for label, value in (("network.pod_cidr", pod_cidr),
                         ("network.service_cidr", service_cidr)):
        try:
            parsed[label] = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            faults.append(f"{label} '{value}' is not a valid CIDR network: {exc}")
    if len(parsed) < 2:
        return faults

    pods = parsed["network.pod_cidr"]
    svcs = parsed["network.service_cidr"]
    if pods.overlaps(svcs):
        faults.append(
            f"network.pod_cidr {pods} overlaps network.service_cidr {svcs} — "
            f"pod and service address space must be disjoint")

    for label, net in parsed.items():
        for sup in _fabric_supernets(all_nodes, fabric_prefix):
            fab = ipaddress.ip_network(sup)
            if net.overlaps(fab):
                faults.append(
                    f"{label} {net} overlaps fabric prefix {fab} — "
                    f"cluster traffic would collide with the fabric")
        for node_name, node in sorted(all_nodes.items()):
            mgmt = node.get("mgmt_ip")
            if not mgmt:
                continue
            if ipaddress.ip_address(mgmt) in net:
                faults.append(
                    f"{label} {net} contains mgmt_ip {mgmt} of node "
                    f"'{node_name}' — cluster traffic would collide with "
                    f"the management network")
    return faults


# ── Netplan helpers ──────────────────────────────────────────────────────────

def _star_hub(all_nodes):
    for name, cfg in all_nodes.items():
        if cfg.get("fabric_router"):
            return name, cfg
    return None, None


def _star_fabric_supernet(all_nodes):
    ips = [ipaddress.ip_address(fn["fabric_ip"])
           for node in all_nodes.values()
           for fn in node.get("fabric_nics", [])]
    if not ips:
        return None
    net = ipaddress.ip_network(f"{min(ips)}/32")
    while not all(ip in net for ip in ips):
        net = net.supernet()
    return str(net)


def _star_routes_for(node_name, node, fn, all_nodes, fabric_prefix, warnings):
    if node.get("fabric_router"):
        return []

    hub_name, hub_cfg = _star_hub(all_nodes)
    if not hub_name:
        return []

    peer = fn.get("peer")
    if peer != hub_name:
        return []

    supernet = _star_fabric_supernet(all_nodes)
    if not supernet:
        return []

    my_ip = fn["fabric_ip"]
    my_pfx = _nic_prefix(fn, fabric_prefix)
    my_net = ipaddress.ip_network(f"{my_ip}/{my_pfx}", strict=False)
    for hub_fn in hub_cfg.get("fabric_nics", []):
        hub_ip = hub_fn["fabric_ip"]
        if ipaddress.ip_address(hub_ip) in my_net:
            return [{"to": supernet, "via": hub_ip}]

    warnings.append(
        f"star spoke '{node_name}' NIC '{fn.get('nic')}' ({my_ip}/{my_pfx}) "
        f"shares no subnet with any NIC on hub '{hub_name}' — no route generated")
    return []


# ── Core parse + validate ────────────────────────────────────────────────────

def _parse_and_validate(profile):
    """Shared core: parses the profile dict, validates, returns context or errors."""
    errors = []
    warnings = []

    site = profile.get("site", {})
    network = profile.get("network", {})
    racks = profile.get("racks", {}) or {}
    storage = profile.get("storage", {}) or {}
    node_profiles = profile.get("node_profiles", {}) or {}

    # S3: type-check top-level blocks before iterating
    if not isinstance(racks, dict):
        errors.append(
            f"'racks' must be a mapping, got {type(racks).__name__}")
        return {"errors": errors, "warnings": warnings}

    site_name = site.get("name", "unknown")
    calico_mode = network.get("calico_mode", "vxlan")
    leafs = network.get("leafs", []) or []
    bgp_block = network.get("bgp", {}) or {}
    cluster_asn = bgp_block.get("cluster_asn", 65100)
    pod_cidr = network.get("pod_cidr", "10.244.0.0/16")
    service_cidr = network.get("service_cidr", "10.96.0.0/12")
    fabric_mtu = network.get("mtu", 9000)
    vxlan_overhead = network.get("vxlan_overhead", 50)
    cp_on_fabric = network.get("control_plane_on_fabric", False)
    topology = network.get("topology", "clos")
    fabric_prefix = network.get("fabric_prefix", 16)
    netplan_renderer = network.get("netplan_renderer", "networkd")

    access = profile.get("access", {})
    default_ansible_user = access.get("ansible_user", "ubuntu")
    default_ssh_key = access.get(
        "ansible_ssh_private_key_file", "~/.ssh/id_ed25519")

    _legacy_ssh_key = site.get("ssh_private_key_file")
    if _legacy_ssh_key:
        warnings.append(
            "site.ssh_private_key_file is deprecated. "
            "Move it to access.ansible_ssh_private_key_file in rack-profile.yaml. "
            f"Auto-migrating for this run: {_legacy_ssh_key}")
        default_ssh_key = _legacy_ssh_key

    ceph_nic = storage.get("ceph_fabric_nic", "")
    ceph_public_net = storage.get("ceph_public_network", "")
    ceph_cluster_net = storage.get("ceph_cluster_network", "")

    # ── Schema validation ────────────────────────────────────────────────────
    schema_errors = []
    for prof_name, prof in node_profiles.items():
        if not isinstance(prof, dict):
            schema_errors.append(f"node_profiles.{prof_name} must be a mapping")
            continue
        _check_keys(prof, VALID_PROFILE_KEYS, f"node_profiles.{prof_name}",
                     schema_errors)
        if "gpu_count" in prof and not isinstance(prof["gpu_count"], int):
            schema_errors.append(
                f"node_profiles.{prof_name}.gpu_count must be an integer, "
                f"got {type(prof['gpu_count']).__name__!r}")

    # H3: validate leaf entries
    for i, leaf in enumerate(leafs):
        if not isinstance(leaf, dict):
            schema_errors.append(f"leafs[{i}] must be a mapping")
            continue
        _check_keys(leaf, VALID_LEAF_KEYS, f"leafs[{i}]", schema_errors)
        if "name" not in leaf:
            schema_errors.append(f"leafs[{i}] is missing required field 'name'")
        if "asn" not in leaf:
            schema_errors.append(
                f"leafs[{i}] ('{leaf.get('name', '?')}') is missing required "
                f"field 'asn'")

    # S3: type-check each rack's nodes block
    # Flatten nodes preserving rack membership.
    all_nodes = {}
    for rack_name, rack in racks.items():
        if not isinstance(rack, dict):
            schema_errors.append(
                f"rack '{rack_name}' must be a mapping, got "
                f"{type(rack).__name__}")
            continue
        nodes_block = rack.get("nodes") or {}
        if not isinstance(nodes_block, dict):
            schema_errors.append(
                f"rack '{rack_name}'.nodes must be a mapping, got "
                f"{type(nodes_block).__name__}")
            continue
        for node_name, node_cfg in nodes_block.items():
            if not isinstance(node_cfg, dict):
                schema_errors.append(
                    f"node '{node_name}' in rack '{rack_name}' must be a "
                    f"mapping")
                continue
            node_cfg["_rack"] = rack_name
            all_nodes[node_name] = node_cfg

    for node_name, node in all_nodes.items():
        _check_keys(node, VALID_NODE_KEYS, f"node '{node_name}'", schema_errors)
        for i, fn in enumerate(node.get("fabric_nics", [])):
            _check_keys(fn, VALID_FABRIC_NIC_KEYS,
                         f"node '{node_name}' fabric_nics[{i}]", schema_errors)
            # H1: require fabric_ip and nic per fabric NIC
            if not fn.get("fabric_ip"):
                schema_errors.append(
                    f"node '{node_name}' fabric_nics[{i}] is missing "
                    f"required field 'fabric_ip'")
            if not fn.get("nic"):
                schema_errors.append(
                    f"node '{node_name}' fabric_nics[{i}] is missing "
                    f"required field 'nic'")
        if "gpu_count" in node and not isinstance(node["gpu_count"], int):
            schema_errors.append(
                f"node '{node_name}' gpu_count must be an integer, "
                f"got {type(node['gpu_count']).__name__!r}")

    if schema_errors:
        errors.extend(schema_errors)
        return {"errors": errors, "warnings": warnings}

    # Storage nodes must also be kube members (Ceph runs as pods)
    for node_name, node in all_nodes.items():
        roles = node.get("roles", [])
        if "storage" in roles and "kube_node" not in roles and "kube_control_plane" not in roles:
            errors.append(
                f"node '{node_name}' has role 'storage' but is not a kube member. "
                f"Add 'kube_node' to its roles — Ceph OSDs run as pods and require kubelet.")

    # Duplicate leaf name detection
    _leaf_name_counts = {}
    for lf in leafs:
        _leaf_name_counts.setdefault(lf["name"], []).append(lf)
    for name, entries in _leaf_name_counts.items():
        if len(entries) > 1:
            errors.append(
                f"duplicate leaf name '{name}' — {len(entries)} entries. "
                f"Each leaf must have a unique name.")

    leaf_by_name = {lf["name"]: lf for lf in leafs}

    # ── Profile resolution ───────────────────────────────────────────────────
    profile_resolve_errors = []
    for node_name, node in all_nodes.items():
        prof_ref = node.get("profile")
        if prof_ref is None:
            continue
        if prof_ref not in node_profiles:
            profile_resolve_errors.append(
                f"node '{node_name}' references unknown profile '{prof_ref}' "
                f"(valid: {', '.join(sorted(node_profiles.keys())) or 'none'})")
            continue
        merged = {**node_profiles[prof_ref], **node}
        all_nodes[node_name] = merged

    if profile_resolve_errors:
        errors.extend(profile_resolve_errors)
        return {"errors": errors, "warnings": warnings}

    # ── Silicon validation ───────────────────────────────────────────────────
    silicon_errors = []
    for node_name, node in all_nodes.items():
        accel = node.get("accelerator")
        node_family = node.get("node_family")
        if accel:
            if not node_family:
                silicon_errors.append(
                    f"node '{node_name}' has accelerator='{accel}' but no node_family "
                    f"(set it directly or via profile:)")
            if not node.get("gpu_count"):
                silicon_errors.append(
                    f"node '{node_name}' has accelerator='{accel}' but no gpu_count "
                    f"— required")
            parts = accel.split("-")
            if len(parts) < 2:
                silicon_errors.append(
                    f"node '{node_name}' accelerator='{accel}' must follow "
                    f"vendor-model[-form] format")
            else:
                vendor = parts[0]
                if vendor not in VALID_ACCELERATOR_VENDORS:
                    silicon_errors.append(
                        f"node '{node_name}' accelerator='{accel}' has unknown vendor "
                        f"'{vendor}' "
                        f"(valid: {', '.join(sorted(VALID_ACCELERATOR_VENDORS))})")
            rdma_nics = node.get("rdma_nics", [])
            form = parts[-1] if len(parts) >= 3 else ""
            if form == "hgx" and not rdma_nics:
                warnings.append(
                    f"node '{node_name}' has form=hgx but no rdma_nics declared")
        elif node.get("gpu_count"):
            silicon_errors.append(
                f"node '{node_name}' has gpu_count but no accelerator — "
                f"gpu_count requires accelerator to be set")

    if silicon_errors:
        errors.extend(silicon_errors)
        return {"errors": errors, "warnings": warnings}

    # Warn for nodes with no node_family (backwards-compat).
    for node_name, node in all_nodes.items():
        if not node.get("node_family") and not node.get("profile") and not node.get("accelerator"):
            warnings.append(
                (f"node '{node_name}' has no node_family or profile — "
                 f"cpu-gen label will not be emitted "
                 f"(backwards-compatible, not an error)"))

    leaf_names = {lf["name"] for lf in leafs}

    # ── Topology / address / duplicate validation ────────────────────────────
    if topology not in ("clos", "star", "l2", "none"):
        errors.append(
            f"network.topology '{topology}' is invalid — "
            f"expected 'clos', 'star', 'l2', or 'none'")

    if calico_mode == "bgp":
        for leaf in leafs:
            if leaf.get("peer_ip", "TBD") == "TBD":
                warnings.append(
                    f"leaf '{leaf['name']}' peer_ip is TBD — configure before "
                    f"enabling BGP")

    for node_name, node in all_nodes.items():
        for fn in node.get("fabric_nics", []):
            if fn.get("leaf") and fn["leaf"] not in leaf_names:
                warnings.append(
                    f"node '{node_name}' references unknown leaf '{fn['leaf']}'")
        for role in node.get("roles", []):
            if role not in KNOWN_ROLES:
                warnings.append(
                    f"node '{node_name}' has unrecognized role '{role}' — "
                    f"no inventory group will be generated for it "
                    f"(known: {', '.join(sorted(KNOWN_ROLES))})")

    # Duplicate address detection + H2: validate IP format.
    _mgmt_claims = {}
    _fabric_claims = {}
    for node_name, node in all_nodes.items():
        mgmt = node.get("mgmt_ip")
        if mgmt:
            if _safe_ip_address(mgmt, f"node '{node_name}' mgmt_ip", errors):
                _mgmt_claims.setdefault(mgmt, []).append(node_name)
        for fn in node.get("fabric_nics", []):
            fip = fn.get("fabric_ip")
            if fip:
                if _safe_ip_address(fip, f"node '{node_name}' fabric_ip", errors):
                    _fabric_claims.setdefault(fip, []).append(
                        f"{node_name}:{fn.get('nic', '?')}")
    for ip, owners in sorted(_mgmt_claims.items()):
        if len(owners) > 1:
            errors.append(f"duplicate mgmt_ip {ip} claimed by: {', '.join(owners)}")
    for ip, owners in sorted(_fabric_claims.items()):
        if len(owners) > 1:
            errors.append(
                f"duplicate fabric_ip {ip} claimed by: {', '.join(owners)}")

    # Storage node NIC validation.
    for node_name, node in all_nodes.items():
        if "storage" not in node.get("roles", []):
            continue
        want = _node_ceph_nic(node, ceph_nic)
        if not want:
            continue
        have = [fn.get("nic") for fn in node.get("fabric_nics", [])]
        if want not in have:
            warnings.append(
                f"storage node '{node_name}' has no NIC named '{want}' "
                f"(ceph_fabric_nic); found: {', '.join(have) or 'none'}")

    # Star topology invariants.
    if topology == "star":
        routers = [n for n, cfg in all_nodes.items() if cfg.get("fabric_router")]
        if len(routers) == 0:
            errors.append(
                "network.topology=star requires exactly one node with "
                "'fabric_router: true' — no node declares it")
        elif len(routers) > 1:
            errors.append(
                "network.topology=star supports one hub, but "
                f"{len(routers)} nodes declare 'fabric_router: true': "
                f"{', '.join(routers)}")
        for node_name, node in all_nodes.items():
            for fn in node.get("fabric_nics", []):
                peer = fn.get("peer")
                if peer and peer not in all_nodes:
                    errors.append(
                        f"node '{node_name}' NIC '{fn.get('nic', '?')}' "
                        f"declares peer '{peer}' which is not a defined node")
    elif any(cfg.get("fabric_router") for cfg in all_nodes.values()):
        warnings.append(
            "'fabric_router: true' is set but network.topology is not 'star' "
            "— the flag will be ignored")

    if topology == "l2":
        if leafs:
            errors.append(
                "network.topology=l2 does not use leafs — remove the 'leafs:' "
                "block (all nodes share one L2 broadcast domain)")
        for node_name, node in all_nodes.items():
            if node.get("fabric_router"):
                errors.append(
                    f"node '{node_name}' sets fabric_router but topology=l2 "
                    f"has no hub — remove fabric_router")
            for fn in node.get("fabric_nics", []):
                if fn.get("peer"):
                    errors.append(
                        f"node '{node_name}' NIC '{fn.get('nic', '?')}' "
                        f"declares 'peer:' but topology=l2 has no "
                        f"point-to-point cables — remove peer")
                if fn.get("leaf"):
                    errors.append(
                        f"node '{node_name}' NIC '{fn.get('nic', '?')}' "
                        f"declares 'leaf:' but topology=l2 has no leaf "
                        f"switches — remove leaf")

    if topology == "none":
        if calico_mode == "bgp":
            errors.append(
                "network.topology=none has no fabric — calico_mode must be "
                "'vxlan', not 'bgp'")
        if cp_on_fabric:
            warnings.append(
                "control_plane_on_fabric=true is ignored when topology=none "
                "(no fabric exists)")
        if leafs:
            errors.append(
                "network.topology=none does not use leafs — remove the "
                "'leafs:' block")
        for node_name, node in all_nodes.items():
            if node.get("fabric_nics"):
                errors.append(
                    f"node '{node_name}' has fabric_nics but topology=none "
                    f"— remove fabric_nics (use mgmt_ip for all traffic)")
            if node.get("fabric_router"):
                errors.append(
                    f"node '{node_name}' sets fabric_router but "
                    f"topology=none — remove it")

    # mgmt_nic deprecation notice.
    if network.get("mgmt_nic") or any(
            cfg.get("mgmt_nic") for cfg in all_nodes.values()):
        warnings.append(
            "'mgmt_nic' is set but is obsolete and ignored. Calico selects "
            "each node's address via kubespray's can-reach=$(NODEIP). "
            "Remove the key.")

    # If any IP addresses are malformed, skip the address-level checks — they
    # would raise ValueError on the same bad input.
    if errors:
        return {"errors": errors, "warnings": warnings}

    # Fabric addressing conflicts.
    for node_name, reason in _fabric_addressing_conflicts(all_nodes, fabric_prefix):
        errors.append(
            f"fabric addressing is ambiguous for node '{node_name}': {reason}. "
            f"Fabric prefixes must isolate each node's primary NIC — check "
            f"'prefix:' on its fabric_nics")

    # Unusable addresses.
    for node_name, reason in _unusable_fabric_addresses(all_nodes, fabric_prefix):
        errors.append(f"node '{node_name}': {reason}")

    # Star cable faults.
    if topology == "star":
        for node_name, reason in _star_cable_faults(all_nodes, fabric_prefix):
            errors.append(f"node '{node_name}': {reason}")

    # Cluster CIDR sanity.
    errors.extend(_cluster_cidr_faults(pod_cidr, service_cidr, all_nodes,
                                       fabric_prefix))

    # Pack everything the generators need into a context dict.
    ctx = {
        "site_name": site_name,
        "calico_mode": calico_mode,
        "leafs": leafs,
        "leaf_by_name": leaf_by_name,
        "cluster_asn": cluster_asn,
        "pod_cidr": pod_cidr,
        "service_cidr": service_cidr,
        "fabric_mtu": fabric_mtu,
        "vxlan_overhead": vxlan_overhead,
        "cp_on_fabric": cp_on_fabric,
        "topology": topology,
        "fabric_prefix": fabric_prefix,
        "netplan_renderer": netplan_renderer,
        "default_ansible_user": default_ansible_user,
        "default_ssh_key": default_ssh_key,
        "ceph_nic": ceph_nic,
        "ceph_public_net": ceph_public_net,
        "ceph_cluster_net": ceph_cluster_net,
        "all_nodes": all_nodes,
        "storage": storage,
        "node_profiles": node_profiles,
        "errors": errors,
        "warnings": warnings,
    }
    return ctx


# ── Generators ───────────────────────────────────────────────────────────────

def _gen_hosts_yaml(ctx):
    c = ctx
    all_nodes = c["all_nodes"]
    storage = c["storage"]

    lines = [
        "# Copyright (C) 2025-2026 Intel Corporation",
        "# SPDX-License-Identifier: Apache-2.0",
        "#",
        "# AUTO-GENERATED by rack_profile_parser",
        f"# Source: rack-profile.yaml (site: {c['site_name']})",
        "# DO NOT EDIT MANUALLY — edit rack-profile.yaml and regenerate.",
        "---",
        "all:",
        "  hosts:",
    ]

    storage_nodes_map = {n["name"]: n for n in storage.get("nodes", [])}

    for name, node in all_nodes.items():
        nics = node.get("fabric_nics", [])
        roles = node.get("roles", [])
        mgmt_ip = node.get("mgmt_ip", "")
        is_cp = "kube_control_plane" in roles or "etcd" in roles
        node_family = node.get("node_family", "")
        sku = node.get("sku", "")
        accel = node.get("accelerator", "")
        gpu_count = node.get("gpu_count", "")

        if nics and (not is_cp or c["cp_on_fabric"]):
            bind_ip = nics[0]["fabric_ip"]
        else:
            bind_ip = mgmt_ip

        lines.append(f"    {name}:")
        # All nodes use SSH — including the bastion (local: true is accepted
        # but ignored so that the deploy node SSHs to itself via mgmt_ip,
        # giving uniform delegate_to behaviour across every fabric role).
        lines.append(f"      ansible_host: {mgmt_ip}")
        lines.append(
            f"      ansible_user: "
            f"{node.get('ansible_user', c['default_ansible_user'])}")
        lines.append(
            "      ansible_ssh_private_key_file: "
            f"{node.get('ansible_ssh_private_key_file', c['default_ssh_key'])}")
        lines.append("      ansible_become: true")
        if bind_ip:
            lines.append(f"      ip: {bind_ip}")
            lines.append(f"      access_ip: {bind_ip}")
        if node.get("hostname"):
            lines.append(f"      node_hostname: {node['hostname']}")

        if nics:
            lines.append("      node_fabric_nics:")
            for fn in nics:
                lines.append(f"        - nic: {fn['nic']}")
                lines.append(f"          fabric_ip: {fn['fabric_ip']}")
                if fn.get("leaf"):
                    lines.append(f"          leaf: {fn['leaf']}")

        if node_family:
            lines.append(f"      node_family: {node_family}")
        if sku:
            lines.append(f"      node_sku: {sku}")
        if accel:
            accel_parts = accel.split("-")
            accel_vendor = accel_parts[0]
            accel_model = accel_parts[1] if len(accel_parts) >= 2 else accel
            accel_form = accel_parts[2] if len(accel_parts) >= 3 else ""
            lines.append(f"      node_accelerator: {accel}")
            lines.append(f"      node_gpu_vendor: {accel_vendor}")
            lines.append(f"      node_gpu_model: {accel_model}")
            if accel_form:
                lines.append(f"      node_gpu_form: {accel_form}")
        if gpu_count:
            lines.append(f"      node_gpu_count: {gpu_count}")

        if name in storage_nodes_map:
            devs = storage_nodes_map[name].get("devices", [])
            if devs:
                lines.append(f"      devices: {devs}")

    def in_role(role):
        return [n for n, cfg in all_nodes.items()
                if role in cfg.get("roles", [])]

    def with_family(family):
        return [n for n, cfg in all_nodes.items()
                if cfg.get("node_family") == family
                and not cfg.get("accelerator")]

    cp = in_role("kube_control_plane")
    workers = in_role("kube_node")
    etcd = in_role("etcd")
    gnr_ap = in_role("gnr_ap")
    ceph_storage = in_role("storage")
    gnr_sp_nodes = with_family("cpu-gnr-sp")
    cwf_nodes = with_family("cpu-cwf")
    spr_nodes = with_family("cpu-spr")
    gpu_nodes = [n for n, cfg in all_nodes.items() if cfg.get("accelerator")]

    lines += ["  children:", "    kube_control_plane:", "      hosts:"]
    for n in cp:
        lines.append(f"        {n}:")
    lines += ["    kube_node:", "      hosts:"]
    for n in workers:
        lines.append(f"        {n}:")
    lines += ["    etcd:", "      hosts:"]
    for n in etcd:
        lines.append(f"        {n}:")
    lines += [
        "    k8s_cluster:",
        "      children:",
        "        kube_control_plane:",
        "        kube_node:",
        "    calico_rr:",
        "      hosts: {}",
    ]
    if gnr_ap:
        lines += ["    gnr_ap:", "      hosts:"]
        for n in gnr_ap:
            lines.append(f"        {n}:")
    if gnr_sp_nodes:
        lines += ["    gnr_sp:", "      hosts:"]
        for n in gnr_sp_nodes:
            lines.append(f"        {n}:")
    if cwf_nodes:
        lines += ["    cwf:", "      hosts:"]
        for n in cwf_nodes:
            lines.append(f"        {n}:")
    if spr_nodes:
        lines += ["    spr:", "      hosts:"]
        for n in spr_nodes:
            lines.append(f"        {n}:")
    if gpu_nodes:
        lines += ["    gpu_nodes:", "      hosts:"]
        for n in gpu_nodes:
            lines.append(f"        {n}:")
    if ceph_storage:
        lines += ["    ceph_storage_nodes:", "      hosts:"]
        for n in ceph_storage:
            lines.append(f"        {n}:")
    ecmp = _ecmp_nodes(all_nodes, c["calico_mode"], c["leaf_by_name"])
    if ecmp:
        lines += ["    ecmp_nodes:", "      hosts:"]
        for n in ecmp:
            lines.append(f"        {n}:")
    fabric_routers = (
        [n for n, cfg in all_nodes.items() if cfg.get("fabric_router")]
        if c["topology"] == "star" else [])
    if fabric_routers:
        lines += ["    fabric_routers:", "      hosts:"]
        for n in fabric_routers:
            lines.append(f"        {n}:")

    return "\n".join(lines) + "\n"


def _gen_fabric_vars(ctx):
    c = ctx
    lines = [
        "# Copyright (C) 2025-2026 Intel Corporation",
        "# SPDX-License-Identifier: Apache-2.0",
        "#",
        "# AUTO-GENERATED by rack_profile_parser",
        f"# Source: rack-profile.yaml (site: {c['site_name']})",
        "# DO NOT EDIT MANUALLY.",
        "---",
        "",
        f"rack_site: {c['site_name']}",
        f"rack_calico_mode: {c['calico_mode']}",
        f"rack_pod_cidr: {c['pod_cidr']}",
        f"rack_service_cidr: {c['service_cidr']}",
        f"rack_cluster_asn: {c['cluster_asn']}",
        f"rack_fabric_mtu: {c['fabric_mtu']}",
        "",
        "rack_fabric_cidrs:",
    ] + [
        f"  - {cidr}"
        for cidr in _fabric_supernets(c["all_nodes"], c["fabric_prefix"])
    ] + [
        "",
        "rack_leafs:",
    ]
    for leaf in c["leafs"]:
        lines += [
            f"  - name: {leaf['name']}",
            f"    asn: {leaf['asn']}",
            f"    peer_ip: {leaf.get('peer_ip', 'TBD')}",
            f"    fabric_subnet: {leaf.get('fabric_subnet', 'TBD')}",
        ]
        if leaf.get("switch_name"):
            lines.append(f"    switch_name: {leaf['switch_name']}")
        if leaf.get("switch_mgmt_ip"):
            lines.append(f"    switch_mgmt_ip: {leaf['switch_mgmt_ip']}")
    lines.append("")
    return "\n".join(lines) + "\n"


def _gen_calico_yml(ctx):
    c = ctx
    lines = [
        "# Copyright (C) 2025-2026 Intel Corporation",
        "# SPDX-License-Identifier: Apache-2.0",
        "#",
        "# AUTO-GENERATED by rack_profile_parser",
        f"# Source: rack-profile.yaml (site: {c['site_name']})",
        "# DO NOT EDIT MANUALLY — edit rack-profile.yaml and regenerate.",
        "---",
        "",
        f"kube_pods_subnet: {c['pod_cidr']}",
        f"kube_service_addresses: {c['service_cidr']}",
        "kube_network_node_prefix: 24",
        "",
    ]
    if c["calico_mode"] == "bgp" and c["topology"] == "l2":
        lines += [
            "calico_network_backend: bird",
            "",
            "calico_ipip_mode: Never",
            "calico_vxlan_mode: Never",
            "",
            "calico_nat_outgoing: true",
            "",
            f"calico_mtu: {c['fabric_mtu']}",
            "",
            f'global_as_num: "{c["cluster_asn"]}"',
            "",
            'calico_node_to_node_mesh: "true"',
        ]
    elif c["calico_mode"] == "bgp":
        lines += [
            "calico_network_backend: bird",
            "",
            "calico_ipip_mode: Never",
            "calico_vxlan_mode: Never",
            "",
            "calico_nat_outgoing: true",
            "",
            f"calico_mtu: {c['fabric_mtu']}",
            "",
            f'global_as_num: "{c["cluster_asn"]}"',
            "",
            'calico_node_to_node_mesh: "false"',
            "",
            "# peer_with_router triggers kubespray to disable node-to-node mesh",
            "# and enables per-node BGPPeer CRD-based ToR peering.",
            "peer_with_router: true",
        ]
    else:
        lines += [
            "calico_network_backend: vxlan",
            "",
            "calico_ipip_mode: Never",
            "calico_vxlan_mode: Always",
            "",
            "calico_nat_outgoing: true",
            "",
            f"calico_mtu: {c['fabric_mtu'] - c['vxlan_overhead']}",
            "",
            'calico_node_to_node_mesh: "true"',
        ]
    return "\n".join(lines) + "\n"


def _gen_ceph_vars(ctx):
    c = ctx
    lines = [
        "# Copyright (C) 2025-2026 Intel Corporation",
        "# SPDX-License-Identifier: Apache-2.0",
        "#",
        "# AUTO-GENERATED by rack_profile_parser",
        f"# Source: rack-profile.yaml (site: {c['site_name']})",
        "# DO NOT EDIT MANUALLY.",
        "---",
        "",
        f'ceph_fabric_nic: "{c["ceph_nic"]}"',
        "",
        f'ceph_public_network: "{c["ceph_public_net"]}"',
        f'ceph_cluster_network: "{c["ceph_cluster_net"]}"',
        "",
        "ceph_replica_size: 3",
        "ceph_storage_nodes:",
    ]
    for node in c["storage"].get("nodes", []):
        lines.append(f'  - name: "{node["name"]}"')
        lines.append("    devices:")
        for dev in node.get("devices", []):
            lines.append(f'      - name: "{dev}"')
    return "\n".join(lines) + "\n"


def _gen_flags_env(ctx):
    c = ctx
    all_nodes = c["all_nodes"]
    controller_has_fabric = any(
        node.get("local") and node.get("fabric_nics")
        for node in all_nodes.values())
    if c["topology"] == "none":
        ping_access_ip = "true"
    else:
        ping_access_ip = ("true"
                          if (c["cp_on_fabric"] or controller_has_fabric)
                          else "false")

    lines = [
        "# Copyright (C) 2025-2026 Intel Corporation",
        "# SPDX-License-Identifier: Apache-2.0",
        "#",
        "# AUTO-GENERATED by rack_profile_parser  —  DO NOT EDIT MANUALLY.",
        "",
        f"rack_scale_cp_on_fabric={str(c['cp_on_fabric']).lower()}",
        f"rack_scale_ping_access_ip={ping_access_ip}",
        f"rack_scale_calico_mode={c['calico_mode']}",
    ]
    if c["topology"] != "clos":
        lines.append(f"rack_scale_topology={c['topology']}")
    ecmp = _ecmp_nodes(all_nodes, c["calico_mode"], c["leaf_by_name"])
    if ecmp:
        lines.append(f"rack_scale_ecmp_nodes={','.join(ecmp)}")

    gpu_vendor = ""
    gpu_model = ""
    gpu_form = ""
    for node in all_nodes.values():
        accel = node.get("accelerator")
        if accel:
            parts = accel.split("-")
            gpu_vendor = parts[0] if len(parts) >= 1 else ""
            gpu_model = parts[1] if len(parts) >= 2 else ""
            gpu_form = parts[2] if len(parts) >= 3 else ""
            break

    lines.append(f"rack_scale_gpu_vendor={gpu_vendor}")
    lines.append(f"rack_scale_gpu_model={gpu_model}")
    lines.append(f"rack_scale_gpu_form={gpu_form}")
    return "\n".join(lines) + "\n"


def _gen_netplan_files(ctx):
    c = ctx
    all_nodes = c["all_nodes"]
    warnings = c["warnings"]
    files = {}

    if c["topology"] == "none":
        return files

    for node_name, node in all_nodes.items():
        nics = node.get("fabric_nics", [])
        if not nics:
            continue

        hostname = node.get("hostname", node_name)
        ethernets = {}

        for fn in nics:
            nic = fn["nic"]
            fab_ip = fn["fabric_ip"]
            leaf_name = fn.get("leaf")
            leaf_cfg = c["leaf_by_name"].get(leaf_name, {})
            gateway = leaf_cfg.get("peer_ip", "")

            if c["topology"] == "star":
                routes = _star_routes_for(
                    node_name, node, fn, all_nodes, c["fabric_prefix"],
                    warnings)
            elif c["topology"] == "l2":
                routes = []
            else:
                if gateway and gateway != "TBD":
                    for other_leaf in c["leafs"]:
                        if (other_leaf["name"] != leaf_name
                                and other_leaf.get("fabric_subnet", "TBD")
                                != "TBD"):
                            routes.append({
                                "to": other_leaf["fabric_subnet"],
                                "via": gateway,
                            })

            iface_cfg = {
                "addresses": [
                    f"{fab_ip}/{_nic_prefix(fn, c['fabric_prefix'])}"],
                "mtu": c["fabric_mtu"],
            }
            if routes:
                iface_cfg["routes"] = routes
            ethernets[nic] = iface_cfg

        netplan = {
            "network": {
                "version": 2,
                "renderer": c["netplan_renderer"],
                "ethernets": {},
            }
        }
        for nic, cfg in ethernets.items():
            entry = {
                "dhcp4": False,
                "dhcp6": False,
                "addresses": cfg["addresses"],
                "mtu": cfg["mtu"],
            }
            if "routes" in cfg:
                entry["routes"] = [
                    {"to": r["to"], "via": r["via"]} for r in cfg["routes"]]
            netplan["network"]["ethernets"][nic] = entry

        content = (
            "# Copyright (C) 2025-2026 Intel Corporation\n"
            "# SPDX-License-Identifier: Apache-2.0\n"
            "#\n"
            "# AUTO-GENERATED by rack_profile_parser\n"
            f"# Node: {hostname} ({node_name})\n"
            "# DO NOT EDIT MANUALLY.\n"
            "#\n"
        )
        content += yaml.dump(netplan, default_flow_style=False,
                             sort_keys=False)
        files[os.path.join("netplan", f"{hostname}.yaml")] = content

    return files


def _gen_bgp_peers(ctx):
    c = ctx
    if c["calico_mode"] != "bgp":
        return {}

    files = {}
    # Track (node, leaf) pairs to disambiguate when a node has multiple
    # NICs on the same leaf — append an index to avoid filename collision.
    _peer_count = {}
    for node_name, node in c["all_nodes"].items():
        nics = node.get("fabric_nics", [])
        for fn in nics:
            leaf_name = fn.get("leaf")
            if not leaf_name or leaf_name not in c["leaf_by_name"]:
                continue
            leaf = c["leaf_by_name"][leaf_name]
            peer_ip = leaf.get("peer_ip", "TBD")
            peer_asn = leaf.get("asn")
            src_ip = fn["fabric_ip"]

            if peer_ip == "TBD" or not peer_asn:
                continue

            key = (node_name, leaf_name)
            idx = _peer_count.get(key, 0)
            _peer_count[key] = idx + 1
            peer_name = f"{node_name}-{leaf_name}" if idx == 0 else f"{node_name}-{leaf_name}-{idx}"
            content = "\n".join([
                "# Copyright (C) 2025-2026 Intel Corporation",
                "# SPDX-License-Identifier: Apache-2.0",
                "#",
                "# AUTO-GENERATED by rack_profile_parser",
                f"# Source: rack-profile.yaml (site: {c['site_name']})",
                "# DO NOT EDIT MANUALLY.",
                "#",
                f"# Per-node BGPPeer: {node_name} -> {leaf_name} ({peer_ip})",
                (f"# sourceAddress: bird uses {fn['nic']} ({src_ip}) as the "
                 f"TCP source."),
                "---",
                "apiVersion: crd.projectcalico.org/v1",
                "kind: BGPPeer",
                "metadata:",
                f"  name: {peer_name}",
                "spec:",
                f"  node: {node_name}",
                f"  peerIP: {peer_ip}",
                f"  asNumber: {peer_asn}",
                f"  sourceAddress: {src_ip}",
                "  numAllowedLocalASNumbers: 1",
            ]) + "\n"

            rel_path = os.path.join(
                "metadata", "bgp-peers", f"{peer_name}.yaml")
            files[rel_path] = content

    return files


def _build_summary(ctx):
    """Build the summary metadata dict."""
    all_nodes = ctx["all_nodes"]
    gpu_vendor = ""
    gpu_model = ""
    gpu_form = ""
    for node in all_nodes.values():
        accel = node.get("accelerator")
        if accel:
            parts = accel.split("-")
            gpu_vendor = parts[0] if len(parts) >= 1 else ""
            gpu_model = parts[1] if len(parts) >= 2 else ""
            gpu_form = parts[2] if len(parts) >= 3 else ""
            break

    return {
        "site": ctx["site_name"],
        "topology": ctx["topology"],
        "calico_mode": ctx["calico_mode"],
        "node_count": len(all_nodes),
        "gpu_vendor": gpu_vendor,
        "gpu_model": gpu_model,
        "gpu_form": gpu_form,
    }


# ── Public filter functions ──────────────────────────────────────────────────

def rack_profile_parse(profile_data):
    """Parse rack-profile.yaml dict and generate all fabric artifacts.

    Returns a dict with string content for each artifact, plus errors/warnings.
    The calling role writes the content to disk.
    """
    profile_data = _to_native(profile_data)
    ctx = _parse_and_validate(profile_data)

    if ctx.get("errors"):
        return {
            "hosts_yaml": "",
            "calico_yml": "",
            "fabric_yml": "",
            "ceph_yml": "",
            "flags_env": "",
            "netplan_files": {},
            "bgp_peer_files": {},
            "errors": ctx["errors"],
            "warnings": ctx.get("warnings", []),
            "summary": {},
        }

    return {
        "hosts_yaml": _gen_hosts_yaml(ctx),
        "calico_yml": _gen_calico_yml(ctx),
        "fabric_yml": _gen_fabric_vars(ctx),
        "ceph_yml": _gen_ceph_vars(ctx),
        "flags_env": _gen_flags_env(ctx),
        "netplan_files": _gen_netplan_files(ctx),
        "bgp_peer_files": _gen_bgp_peers(ctx),
        "errors": [],
        "warnings": ctx["warnings"],
        "summary": _build_summary(ctx),
    }


def rack_profile_validate(profile_data):
    """Run validation only — no artifact generation.

    Returns {'valid': bool, 'errors': [], 'warnings': []}.
    """
    profile_data = _to_native(profile_data)
    ctx = _parse_and_validate(profile_data)
    errs = ctx.get("errors", [])
    return {
        "valid": len(errs) == 0,
        "errors": errs,
        "warnings": ctx.get("warnings", []),
    }


class FilterModule:
    def filters(self):
        return {
            "rack_profile_parse": rack_profile_parse,
            "rack_profile_validate": rack_profile_validate,
        }
