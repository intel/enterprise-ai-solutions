# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import importlib.util
import pathlib

_PLUGIN = pathlib.Path(__file__).resolve().parents[3] / "filter_plugins" / "apiserver_source_ips.py"
_spec = importlib.util.spec_from_file_location("apiserver_source_ips", _PLUGIN)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
apiserver_source_ips = _mod.apiserver_source_ips


def node(internal=(), annotations=None, extra_addresses=()):
    addrs = [{"type": "InternalIP", "address": a} for a in internal]
    addrs += [{"type": t, "address": a} for t, a in extra_addresses]
    return {"metadata": {"annotations": annotations or {}}, "status": {"addresses": addrs}}


def test_single_node_without_annotations():
    assert apiserver_source_ips([node(["10.0.0.1"])]) == ["10.0.0.1"]


def test_vxlan_tunnel_addresses_follow_internal_ips():
    nodes = [
        node(["10.0.0.1"], {"projectcalico.org/IPv4VXLANTunnelAddr": "10.233.1.128"}),
        node(["10.0.0.2"], {"projectcalico.org/IPv4VXLANTunnelAddr": "10.233.2.128"}),
    ]
    assert apiserver_source_ips(nodes) == ["10.0.0.1", "10.0.0.2", "10.233.1.128", "10.233.2.128"]


def test_ipv6_wireguard_and_ipip():
    nodes = [node(["fd00::1"], {
        "projectcalico.org/IPv6WireguardInterfaceAddr": "fd00:10::1",
        "projectcalico.org/IPv4IPIPTunnelAddr": "10.233.3.1",
    })]
    assert apiserver_source_ips(nodes) == ["fd00::1", "10.233.3.1", "fd00:10::1"]


def test_only_internal_ips_and_known_annotations():
    nodes = [node(["10.0.0.1"], {"projectcalico.org/IPv4Address": "10.0.0.1/24", "other": "1.2.3.4"},
                  extra_addresses=[("ExternalIP", "203.0.113.9"), ("Hostname", "cp1")])]
    assert apiserver_source_ips(nodes) == ["10.0.0.1"]


def test_cidr_or_junk_in_an_annotation_cannot_widen_the_policy():
    nodes = [node(["10.0.0.1"], {
        "projectcalico.org/IPv4VXLANTunnelAddr": "0.0.0.0/0",
        "projectcalico.org/IPv4IPIPTunnelAddr": "not-an-ip",
        "projectcalico.org/IPv6VXLANTunnelAddr": "::/0",
    })]
    assert apiserver_source_ips(nodes) == ["10.0.0.1"]


def test_duplicates_removed_and_missing_fields_tolerated():
    nodes = [node(["10.0.0.1"]), node(["10.0.0.1"]), {"metadata": {}}, {"status": {"addresses": None}}]
    assert apiserver_source_ips(nodes) == ["10.0.0.1"]
    assert apiserver_source_ips(None) == []
