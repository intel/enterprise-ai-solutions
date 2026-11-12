#!/bin/bash
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# netplan apply runs after the addresses are deleted: it puts back an address
# another netplan file also sets on the NIC (a site file for an RDMA network, say).
# The config is checked with netplan get, not generate: apply reloads networkd
# only if it finds netplan's networkd files from before its own generate.
#   FABRIC_FILES      the fabric netplan files
#   FABRIC_ADDRESSES  "NIC=ADDRESS/PREFIX ..." that the fabric file assigned
marker=${FABRIC_REAPPLY_MARKER:-/run/fabric-netplan-reapply}
pending() {
    [ -e "$marker" ] && return 0
    for f in $FABRIC_FILES; do
        [ -e "$f" ] && return 0
    done
    return 1
}
on_nics=()
for pair in $FABRIC_ADDRESSES; do
    addr=${pair#*=}
    [ -n "$(ip -o addr show dev "${pair%%=*}" to "${addr%/*}" 2>/dev/null)" ] && on_nics+=("$pair")
done
pending || [ ${#on_nics[@]} -gt 0 ] || exit 0

touch "$marker" || { echo "cannot create $marker" >&2; exit 1; }
echo fabric-netplan-changed
for f in $FABRIC_FILES; do
    rm -f "$f" || { echo "cannot remove $f: fix it, then re-run" >&2; exit 1; }
done
netplan get >/dev/null || { echo "netplan get fails: the fabric netplan files are removed and no address was; fix what it reports, then re-run" >&2; exit 1; }
rc=0
for pair in "${on_nics[@]}"; do
    # ip exits 2 when the address is already gone.
    if ip addr del "${pair#*=}" dev "${pair%%=*}"; then
        echo "removed ${pair#*=} from ${pair%%=*}"
    elif [ $? -ne 2 ]; then
        echo "cannot remove ${pair#*=} from ${pair%%=*}" >&2
        rc=1
    fi
done
if netplan apply; then
    rm -f "$marker"
else
    echo "netplan apply fails: an address another netplan file sets is missing until it succeeds; re-run" >&2
    rc=1
fi
exit $rc
