# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import json
import os
import subprocess
import sys
from collections import namedtuple

import pytest
import yaml
from ansible_render import render
from plugin_loader import ROOT

ROLE = ROOT / "roles" / "fabric_netplan"
SCRIPT = ROLE / "files" / "remove_fabric_netplan.sh"
TASK = next(t for t in yaml.safe_load((ROLE / "tasks" / "teardown.yaml").read_text())
            if t["name"] == "fabric_netplan | teardown | Remove the fabric netplan and addresses, then re-apply netplan")
N1 = "ens1=10.0.0.1/24 ens2=10.0.1.1/24 "
BOTH_ON_NICS = (("ens1", "10.0.0.1"), ("ens2", "10.0.1.1"))
STAND_INS = {
    "ip": """
cmd = " ".join(sys.argv[1:])
if cmd.startswith("-o addr show"):
    print("found" if cmd in config["on_nics"] else "")
    sys.exit(0)
log("ip " + cmd)
sys.exit(config["rc"].get(cmd, 0))
""",
    "netplan": """
log("netplan " + sys.argv[1])
sys.exit(config["rc"].get("netplan " + sys.argv[1], 0))
""",
}
Run = namedtuple("Run", "code calls stdout stderr changed")


@pytest.fixture
def node(tmp_path):
    """Run the script on a fake node: on_nics are the (NIC, address) pairs ip finds, rc maps a command to its exit code."""
    for name, body in STAND_INS.items():
        (tmp_path / name).write_text(
            "#!%s\nimport json, sys\nconfig = json.load(open('config.json'))\n"
            "def log(line):\n    open('log', 'a').write(line + '\\n')\n%s" % (sys.executable, body))
        (tmp_path / name).chmod(0o755)
    fabric_files = [tmp_path / "99-fabric.yaml", tmp_path / "60-fabric.yaml"]

    def run(addresses=N1, on_nics=BOTH_ON_NICS, files=("99-fabric.yaml",), rc=None, marker=tmp_path / "marker"):
        for f in files:
            (tmp_path / f).write_text("network: {}\n")
        (tmp_path / "config.json").write_text(json.dumps(
            {"on_nics": ["-o addr show dev %s to %s" % pair for pair in on_nics], "rc": rc or {}}))
        (tmp_path / "log").write_text("")
        env = dict(os.environ, PATH="%s:%s" % (tmp_path, os.environ["PATH"]), FABRIC_ADDRESSES=addresses,
                   FABRIC_FILES=" ".join(str(f) for f in fabric_files), FABRIC_REAPPLY_MARKER=str(marker))
        done = subprocess.run(["bash", str(SCRIPT)], cwd=tmp_path, env=env, check=False,
                              capture_output=True, text=True)
        changed = render("{{ %s }}" % TASK["changed_when"], {"_fabric_netplan_removed": {"stdout": done.stdout}})
        return Run(done.returncode, (tmp_path / "log").read_text().splitlines(), done.stdout.splitlines(),
                   done.stderr, changed in (True, "True"))
    run.marker = tmp_path / "marker"
    run.files_left = lambda: [f.name for f in fabric_files if f.exists()]
    return run


def test_the_fabric_netplan_and_addresses_are_removed_then_netplan_is_reapplied(node):
    run = node()
    assert run.code == 0 and run.stderr == "" and run.changed
    assert run.calls == ["netplan get", "ip addr del 10.0.0.1/24 dev ens1", "ip addr del 10.0.1.1/24 dev ens2",
                         "netplan apply"]
    assert run.stdout == ["fabric-netplan-changed", "removed 10.0.0.1/24 from ens1", "removed 10.0.1.1/24 from ens2"]
    assert node.files_left() == [] and not node.marker.exists()


def test_only_the_addresses_still_on_a_nic_are_removed(node):
    assert node(on_nics=(("ens2", "10.0.1.1"),)).calls == ["netplan get", "ip addr del 10.0.1.1/24 dev ens2",
                                                           "netplan apply"]


def test_an_address_already_gone_is_not_a_failure(node):
    assert node(rc={"addr del 10.0.0.1/24 dev ens1": 2}).code == 0


def test_netplan_is_reapplied_when_a_removal_fails(node):
    run = node(rc={"addr del 10.0.0.1/24 dev ens1": 1})
    assert run.code != 0 and run.calls[-1] == "netplan apply" and len(run.calls) == 4
    assert run.stderr == "cannot remove 10.0.0.1/24 from ens1: fix it, then re-run\n" and node.marker.exists()
    rerun = node(on_nics=(("ens1", "10.0.0.1"),), files=())
    assert rerun.code == 0 and rerun.calls == ["netplan get", "ip addr del 10.0.0.1/24 dev ens1", "netplan apply"]
    assert not node.marker.exists()


def test_a_failed_apply_is_finished_by_the_next_run(node):
    run = node(rc={"netplan apply": 1})
    assert run.code != 0 and run.stderr.startswith("netplan apply fails") and run.changed and node.marker.exists()
    rerun = node(on_nics=(), files=())
    assert (rerun.code, rerun.calls, rerun.changed) == (0, ["netplan get", "netplan apply"], True)
    assert not node.marker.exists()


def test_a_config_netplan_cannot_read_removes_no_address_and_the_next_run_finishes(node):
    run = node(rc={"netplan get": 1})
    assert run.code != 0 and run.calls == ["netplan get"] and "no address was" in run.stderr
    assert node.files_left() == [] and node.marker.exists()
    assert node(files=()).calls[-1] == "netplan apply" and not node.marker.exists()


def test_a_fabric_file_that_cannot_be_removed_stops_the_node(node, tmp_path):
    (tmp_path / "99-fabric.yaml").mkdir()
    run = node(files=())
    assert run.code != 0 and run.calls == [] and "99-fabric.yaml: fix it, then re-run" in run.stderr
    assert node.marker.exists()


def test_a_marker_that_cannot_be_created_stops_the_node(node, tmp_path):
    run = node(marker=tmp_path / "missing" / "marker")
    assert run.code != 0 and run.calls == [] and "\ncannot create " in run.stderr


@pytest.mark.parametrize("addresses, on_nics", [
    (N1, BOTH_ON_NICS),  # a site file sets them too, so they are back after a teardown
    (N1, ()),
    ("", ()),
])
def test_a_node_without_a_fabric_file_or_marker_is_left_alone(node, addresses, on_nics):
    run = node(addresses=addresses, on_nics=on_nics, files=())
    assert (run.code, run.calls, run.stdout, run.changed) == (0, [], [], False)


def test_an_address_on_another_nic_does_not_count(node):
    assert node(on_nics=(("ens9", "10.0.0.1"),)).calls == ["netplan get", "netplan apply"]


def test_the_old_fabric_file_alone_is_enough(node):
    run = node(addresses="", on_nics=(), files=("60-fabric.yaml",))
    assert (run.code, run.calls, run.changed) == (0, ["netplan get", "netplan apply"], True)
    assert node.files_left() == []


def test_the_task_passes_each_node_its_own_addresses_and_both_fabric_files():
    addresses = [{"node": "n1", "nic": "ens1", "addr": "10.0.0.1/24"},
                 {"node": "n2", "nic": "ens1", "addr": "10.0.0.2/24"},
                 {"node": "n1", "nic": "ens2", "addr": "10.0.1.1/24"}]
    assert render(TASK["environment"]["FABRIC_ADDRESSES"], {"_fabric_addresses": addresses, "item": "n1"}) == N1
    files = {"rack_scale_netplan_dest": "/etc/netplan/99-fabric.yaml", "rack_scale_netplan_old": "/etc/netplan/60-fabric.yaml"}
    assert render(TASK["environment"]["FABRIC_FILES"], files) == "/etc/netplan/99-fabric.yaml /etc/netplan/60-fabric.yaml"
