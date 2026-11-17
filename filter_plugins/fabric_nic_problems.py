# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""How a node's fabric NICs differ from its fabric netplan file.

    {{ ip_addr | fabric_nic_problems(ethernets) }}

ip_addr is `ip -j addr show` from the node, parsed; ethernets is network.ethernets
of the node's fabric netplan file.
"""

import ipaddress


def _addresses(values):
    return {ipaddress.ip_interface(v) for v in values}


def fabric_nic_problems(links, ethernets):
    """{"extra": [...], "wrong": [...]}, one "<nic>: <what>" line per difference.

    extra: addresses the file does not set. wrong: a missing or down NIC, a
    missing address, another MTU, or a bond, bridge or VRF. Only global addresses
    of the families the file sets count: router advertisements can add IPv6
    addresses to an IPv4 fabric. A NIC that is up without carrier is not
    configured yet, so only its extra addresses and bond, bridge or VRF count;
    fabric_validate reports the link. netplan also matches a NIC by its altnames.
    """
    by_name = {name: link for link in links
               for name in [link["ifname"], *link.get("altnames", [])]}
    extra, wrong = [], []
    for nic, want in ethernets.items():
        link = by_name.get(nic)
        if link is None:
            wrong.append("%s: no such NIC" % nic)
            continue
        wanted = _addresses(want.get("addresses", []))
        families = {a.version for a in wanted}
        have = {a for a in _addresses("%s/%s" % (i["local"], i["prefixlen"])
                                      for i in link.get("addr_info", [])
                                      if i.get("scope") == "global")
                if a.version in families}
        extra += ["%s: has %s" % (nic, a) for a in sorted(have - wanted, key=str)]
        if "UP" not in link.get("flags", []):
            wrong.append("%s: is down" % nic)
        if "NO-CARRIER" not in link.get("flags", []):
            wrong += ["%s: %s is missing" % (nic, a)
                      for a in sorted(wanted - have, key=str)]
            mtu = link.get("mtu")
            if "mtu" in want and str(mtu) != str(want["mtu"]):
                wrong.append("%s: MTU is %s, not %s" % (nic, mtu, want["mtu"]))
        if link.get("master"):
            wrong.append("%s: is in %s" % (nic, link["master"]))
    return {"extra": extra, "wrong": wrong}


class FilterModule:
    def filters(self):
        return {"fabric_nic_problems": fabric_nic_problems}
