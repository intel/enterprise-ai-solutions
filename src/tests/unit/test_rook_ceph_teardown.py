# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import os
import pathlib
import signal
import subprocess
import time

import pytest
import yaml
from ansible_render import render

_ROLE = pathlib.Path(__file__).resolve().parents[3] / "roles" / "rook_ceph_storage"
_DEFAULTS = yaml.safe_load((_ROLE / "defaults" / "main.yaml").read_text())
_TEARDOWN = yaml.safe_load((_ROLE / "tasks" / "teardown.yaml").read_text())
DEVICES = {"store1": ["/dev/nvme1n1"] * 5, "store2": ["/dev/nvme1n1"]}


def task(name):
    return next(t for t in _TEARDOWN if t.get("name", "").endswith(name))


def retries(node, wipe=True, devices=DEVICES, **overrides):
    """Polls of 5 s the teardown gives the cleanup pod on node."""
    verify = task("Verify cleanup completed on every node")
    variables = dict(_DEFAULTS, rook_ceph_teardown_wipe_devices=wipe, rook_ceph_devices=devices,
                     item={"metadata": {"name": "rook-ceph-cleanup-x"}, "spec": {"nodeName": node}}, **overrides)
    variables["_rook_node_devices"] = render(verify["vars"]["_rook_node_devices"], variables)
    return int(render(verify["retries"], variables))


@pytest.mark.parametrize("node, wipe, devices, expected", [
    ("store1", True, DEVICES, 312),  # 60 s + 5 devices x 300 s
    ("store2", True, DEVICES, 72),
    ("gpu1", True, DEVICES, 12),  # no OSD devices: host paths only
    ("store1", False, DEVICES, 12),
    ("store1", True, {}, 12),
])
def test_each_pod_waits_for_the_disks_of_its_node(node, wipe, devices, expected):
    assert retries(node, wipe, devices) == expected


def test_the_wait_per_device_can_be_set():
    assert retries("store1", rook_ceph_teardown_wipe_seconds_per_device=600) == 612


def test_the_nodes_without_cleaned_are_the_failures():
    expression = task("Nodes whose cleanup did not log CLEANED")["ansible.builtin.set_fact"]["_rook_wipe_failed"]
    results = [{"item": {"spec": {"nodeName": "store1"}}, "log": "discarding /dev/nvme1n1\nCLEANED"},
               {"item": {"spec": {"nodeName": "store2"}}, "log": "REFUSE /dev/sda (mounted on the host)\nCLEANUP_FAILED"}]
    results.append({"item": {"spec": {"nodeName": "store3"}}, "log": "discarding /dev/nvme1n1"})  # ran out of time
    results.append({"item": {"spec": {"nodeName": "store4"}}, "failed": True, "msg": "pods not found"})  # no log
    failed = render(expression, {"_rook_cleanup_log": {"results": results}})
    assert (failed if isinstance(failed, list) else yaml.safe_load(failed)) == ["store2", "store3", "store4"]


def test_a_cleanup_namespace_left_by_an_earlier_run_goes_first():
    names = [t.get("name", "") for t in _TEARDOWN]
    first = names.index(task("Remove cleanup namespace left by an earlier run")["name"])
    assert first < names.index(task("Ensure cleanup namespace")["name"]) < names.index(task("Create device-map ConfigMap")["name"])


def test_a_cleanup_namespace_that_does_not_go_stops_the_teardown():
    old = task("Remove cleanup namespace left by an earlier run")
    assert old["kubernetes.core.k8s"]["wait"] is True and not {"failed_when", "ignore_errors"} & old.keys()


def test_a_finished_cleanup_pod_stays_until_removed_then_exits_at_once():
    ds = task("disk cleanup DaemonSet")["kubernetes.core.k8s"]["definition"]
    script = ds["spec"]["template"]["spec"]["containers"][0]["args"][0]
    pod = subprocess.Popen(["bash", "-c", script[script.index("trap "):]], start_new_session=True)
    try:
        time.sleep(0.5)
        assert pod.poll() is None
        pod.send_signal(signal.SIGTERM)
        assert pod.wait(timeout=5) == 0
    finally:
        os.killpg(pod.pid, signal.SIGKILL)  # its sleep, which a container's end would take with it
