#!/bin/bash
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# netplan apply runs after the addresses are deleted: it puts back an address
# another netplan file also sets on the NIC (a site file for an RDMA network, say).
# The config is checked with netplan get, not generate: apply reloads networkd
# only if it finds netplan's networkd files from before its own generate.
marker=${FABRIC_REAPPLY_MARKER:-/run/fabric-netplan-reapply}
# After a teardown, an address a site file also sets is back on its NIC: leave it.
pending=
for f in "$marker" $FABRIC_FILES; do
    [ -e "$f" ] && pending=1
done
[ -n "$pending" ] || exit 0

touch "$marker" || { echo "cannot create $marker" >&2; exit 1; }
echo fabric-netplan-changed
for f in $FABRIC_FILES; do
    rm -f "$f" || { echo "cannot remove $f: fix it, then re-run" >&2; exit 1; }
done
netplan get >/dev/null || { echo "netplan get fails: the fabric netplan files are removed and no address was; fix what it reports, then re-run" >&2; exit 1; }
rc=0
for pair in $FABRIC_ADDRESSES; do
    nic=${pair%%=*} addr=${pair#*=}
    [ -n "$(ip -o addr show dev "$nic" to "${addr%/*}" 2>/dev/null)" ] || continue
    # ip exits 2 when the address is already gone.
    if ip addr del "$addr" dev "$nic"; then
        echo "removed $addr from $nic"
    elif [ $? -ne 2 ]; then
        echo "cannot remove $addr from $nic: fix it, then re-run" >&2
        rc=1
    fi
done
if netplan apply; then
    [ "$rc" -eq 0 ] && rm -f "$marker"
else
    echo "netplan apply fails: an address another netplan file sets is missing until it succeeds; re-run" >&2
    rc=1
fi
exit $rc
