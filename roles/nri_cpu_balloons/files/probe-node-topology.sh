#!/bin/sh
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# POSIX-sh node topology probe. Reads ONLY /sys and /proc — no lscpu, no
# Python, no root, no writable filesystem, no network. Runs inside a minimal
# (busybox/alpine) unprivileged pod via `k8s_exec`, or standalone on a host.
#
# Emits KEY=VALUE lines consumed by the nri_cpu_balloons role:
#   NRI_NUMA=<n>                 NUMA node count
#   NRI_LOGICAL=<n>              logical CPU count
#   NRI_TPC=<n>                  threads per core
#   NRI_PHYSICAL=<n>             physical core count (logical / tpc)
#   NRI_PKGNODE={p:[n,...],...}  package->NUMA-node map (PKG_NODE literal for
#                                gen-balloon-types.py)
#   NRI_NODECPUS_<i>=<cpulist>   per-NUMA cpulist (e.g. "0-63,128-191")
#   NRI_SIBLINGS=<csv>           all hyperthread-sibling CPU IDs (the non-lowest
#                                thread of each physical core), sorted
#
# Intentionally uses only: ls, wc, cat, cut, awk, tr, sort, paste — all present
# in busybox.

set -eu

SYSFS=${NRI_PROBE_SYSFS:-/sys}  # a fake tree, for tests
SYS_NODE=$SYSFS/devices/system/node
SYS_CPU=$SYSFS/devices/system/cpu

# Number of CPUs in a cpulist such as "0-3,8,10-11".
count_cpus() {
    echo "$1" | tr ',' '\n' | awk -F- 'NF==2 {n+=$2-$1+1} NF==1 && $1!="" {n++} END {print n+0}'
}

# Online CPUs only: offline ones cannot be pinned.
online=$(cat "$SYS_CPU/online")
logical=$(count_cpus "$online")

# threads per core = number of entries in cpu0's thread_siblings_list
tpc=$(awk -F, '{print NF}' "$SYS_CPU"/cpu0/topology/thread_siblings_list 2>/dev/null || echo 1)
[ "$tpc" -ge 1 ] 2>/dev/null || tpc=1

# Per-NUMA cpulist + package id of each NUMA node's first CPU. Memory-only
# NUMA nodes (CXL, HBM) have no CPUs to pin and are left out.
numa=0
pk=""
for n in "$SYS_NODE"/node[0-9]*; do
    [ -e "$n" ] || continue
    nid=${n##*/node}
    cpulist=$(cat "$n/cpulist")
    [ -n "$cpulist" ] || continue
    numa=$((numa + 1))
    echo "NRI_NODECPUS_${nid}=${cpulist}"
    first=$(echo "$cpulist" | cut -d, -f1 | cut -d- -f1)
    pkg=$(cat "$SYS_CPU/cpu${first}/topology/physical_package_id")
    pk="${pk}${pkg}:${nid} "
done

# Sibling CPUs: an online CPU that is NOT the lowest-numbered thread in its
# own thread_siblings_list (that lowest one is the primary/physical thread).
sib=""
nsib=0
for c in "$SYS_CPU"/cpu[0-9]*; do
    [ -e "$c/topology/thread_siblings_list" ] || continue  # offline
    cid=${c##*/cpu}
    low=$(cut -d, -f1 "$c/topology/thread_siblings_list" | cut -d- -f1)
    if [ "$cid" != "$low" ]; then sib="$sib $cid"; nsib=$((nsib + 1)); fi
done
phys=$(( logical - nsib ))

echo "NRI_NUMA=$numa"
echo "NRI_LOGICAL=$logical"
echo "NRI_TPC=$tpc"
echo "NRI_PHYSICAL=$phys"
echo "NRI_SIBLINGS=$(echo $sib | tr ' ' '\n' | sort -n | paste -sd,)"

# Fold "pkg:node" pairs into a Python-dict literal {pkg:[nodes],...}.
# Keys are emitted in numeric package order via a plain insertion sort over the
# collected keys — no asort/asorti (busybox awk does not provide them).
echo "$pk" | tr ' ' '\n' | awk -F: '
    NF==2 { if (!($1 in m)) keys[++nk]=$1; m[$1]=m[$1]","$2 }
    END {
        for (a=2; a<=nk; a++) { t=keys[a]; b=a-1
            while (b>=1 && (keys[b]+0)>(t+0)) { keys[b+1]=keys[b]; b-- }
            keys[b+1]=t }
        printf "NRI_PKGNODE={"
        for (i=1; i<=nk; i++) { p=keys[i]; s=m[p]; sub(/^,/,"",s)
            if (i>1) printf ","; printf "%s:[%s]", p, s }
        printf "}\n"
    }'
