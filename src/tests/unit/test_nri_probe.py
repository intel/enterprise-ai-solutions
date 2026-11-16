# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
"""nri_cpu_balloons topology probe and reserved-CPU script on fake NUMA layouts: no CPU layout is assumed."""
import os
import subprocess  # nosec B404
from pathlib import Path

import pytest

ROLE = Path(__file__).resolve().parents[3] / "roles" / "nri_cpu_balloons" / "files"


def cpulist(cpus):
    out = []
    for c in sorted(cpus):
        if out and c == out[-1][1] + 1:
            out[-1][1] = c
        else:
            out.append([c, c])
    return ",".join(f"{a}-{b}" if a != b else str(a) for a, b in out)


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")


def fake_sysfs(root, numa, package, sibling, offline=(), memory_only=()):
    """A /sys tree: numa = {node: [physical cores]}, sibling(core) = its second thread."""
    cpu, node = root / "devices/system/cpu", root / "devices/system/node"
    owner = {t: n for n, cores in numa.items() for c in cores for t in (c, sibling(c))}
    for c, n in owner.items():
        (cpu / f"cpu{c}").mkdir(parents=True, exist_ok=True)
        if c in offline:
            continue  # an offline CPU has no topology directory
        core = c if c in numa[n] else next(x for x in numa[n] if sibling(x) == c)
        write(cpu / f"cpu{c}/topology/thread_siblings_list",
              cpulist(t for t in (core, sibling(core)) if t not in offline))
        write(cpu / f"cpu{c}/topology/physical_package_id", str(package[n]))
    write(cpu / "online", cpulist(c for c in owner if c not in offline))
    for n in numa:
        write(node / f"node{n}/cpulist", cpulist(c for c in owner if owner[c] == n and c not in offline))
    for n in memory_only:
        write(node / f"node{n}/cpulist", "")


def probe(root):
    run = subprocess.run(["sh", str(ROLE / "probe-node-topology.sh")], capture_output=True, text=True,  # nosec
                         env={**os.environ, "NRI_PROBE_SYSFS": str(root)}, check=True)
    return dict(line.split("=", 1) for line in run.stdout.splitlines())


@pytest.mark.parametrize("layout, want", [
    # 2 sockets x SNC2, SMT2, a memory-only (CXL) NUMA node 4: left out, not a failure.
    (dict(numa={n: list(range(n * 4, n * 4 + 4)) for n in range(4)}, package={0: 0, 1: 0, 2: 1, 3: 1},
          sibling=lambda c: c + 16, memory_only=[4]),
     {"NRI_NUMA": "4", "NRI_LOGICAL": "32", "NRI_PHYSICAL": "16", "NRI_TPC": "2", "NRI_PKGNODE": "{0:[0,1],1:[2,3]}",
      "NRI_NODECPUS_0": "0-3,16-19", "NRI_NODECPUS_3": "12-15,28-31"}),
    # cpu 3 and its sibling 11 offline: not counted, not pinnable.
    (dict(numa={0: [0, 1, 2, 3], 1: [4, 5, 6, 7]}, package={0: 0, 1: 0}, sibling=lambda c: c + 8, offline=[3, 11]),
     {"NRI_NUMA": "2", "NRI_LOGICAL": "14", "NRI_PHYSICAL": "7", "NRI_TPC": "2",
      "NRI_NODECPUS_0": "0-2,8-10", "NRI_SIBLINGS": "8,9,10,12,13,14,15"}),
    # Interleaved numbering: NUMA 0 = even CPUs, NUMA 1 = odd.
    (dict(numa={0: [0, 2, 4, 6], 1: [1, 3, 5, 7]}, package={0: 0, 1: 1}, sibling=lambda c: c + 8),
     {"NRI_NUMA": "2", "NRI_LOGICAL": "16", "NRI_PHYSICAL": "8", "NRI_PKGNODE": "{0:[0],1:[1]}",
      "NRI_NODECPUS_0": "0,2,4,6,8,10,12,14", "NRI_NODECPUS_1": "1,3,5,7,9,11,13,15"}),
], ids=["snc2-with-memory-only-node", "offline-cpus", "interleaved"])
def test_probe_reports_the_real_layout(tmp_path, layout, want):
    fake_sysfs(tmp_path, **layout)
    got = probe(tmp_path)
    assert {k: got.get(k) for k in want} == want


def test_probe_with_a_memory_only_node_between_cpu_nodes(tmp_path):
    # NUMA ids 0 and 2 have CPUs, 1 is memory-only: ids keep their gap.
    fake_sysfs(tmp_path, numa={0: [0, 1], 2: [2, 3]}, package={0: 0, 2: 0}, sibling=lambda c: c + 4, memory_only=[1])
    got = probe(tmp_path)
    assert got["NRI_NUMA"] == "2" and got["NRI_NODECPUS_2"] == "2-3,6-7" and "NRI_NODECPUS_1" not in got


@pytest.mark.parametrize("profile", ["standard", "siblings"])
def test_reserved_cpus_use_the_real_numa_ids(profile):
    # The reserved-CPU script must iterate the NUMA ids it is given, not 0..N-1.
    env = {**os.environ, "NRI_TOPO_NUMA": "2", "NRI_TOPO_TPC": "2", "NRI_TOPO_LOGICAL": "64",
           "NRI_TOPO_SIBLINGS": cpulist(range(32, 64)), "NRI_TOPO_NODECPUS": "0:0-15,32-47;2:16-31,48-63"}
    run = subprocess.run(["bash", str(ROLE / "compute_reserved_cpus_advanced.sh"), "--profile", profile,  # nosec
                          "--strategy", "balanced", "--ht-strategy", "balanced"],
                         capture_output=True, text=True, env=env, check=False)
    assert run.returncode == 0, run.stderr
    reserved = next(line.split("=", 1)[1] for line in run.stdout.splitlines()
                    if line.startswith("NRI_RESERVED_CPU_LIST="))
    cpus = {int(c) for part in reserved.split(",") for c in
            (range(int(part.split("-")[0]), int(part.split("-")[-1]) + 1))}
    assert cpus and cpus <= set(range(64))
    assert any(c in range(16, 32) or c in range(48, 64) for c in cpus)  # NUMA 2 is used, not skipped
