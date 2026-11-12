# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import ast
import pathlib
import shutil
import subprocess

import pytest
import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

try:
    from ansible.template import trust_as_template
except ImportError:  # ansible-core < 2.19 templates plain strings
    def trust_as_template(source):
        return source

_CHECK = next(t for t in yaml.safe_load((pathlib.Path(__file__).resolve().parents[3]
                                         / "roles" / "fabric_inventory" / "tasks" / "install.yaml").read_text())
              if t["name"].endswith("Check the NIC MTU of each node (topology none)"))


def node(name, ip=None):
    return {"key": name, "value": {"ip": ip} if ip else {}}


# As the shell module returns them: a failed probe has rc != 0, its reason on stderr and the module's msg.
RESULTS = [{"item": node("n1"), "rc": 0, "stdout": "ens259f0np0 1500", "stderr": ""},
           {"item": node("n2"), "rc": 0, "stdout": "eno1 9000", "stderr": ""},
           {"item": node("n3"), "rc": 1, "stdout": "", "stderr": "no NIC holds its IP", "msg": "non-zero return code"},
           {"item": node("n4"), "rc": 127, "stdout": "", "stderr": "/bin/sh: bash: not found", "msg": "non-zero return code"}]


def task(name):
    return next(t for t in _CHECK["block"] if t["name"].endswith(name))


def render(source, variables):
    out = Templar(loader=DataLoader(), variables=variables).template(trust_as_template(source))
    return ast.literal_eval(out) if isinstance(out, str) and out[:1] == "[" else out


def found(mtu=9000):
    facts = task("Nodes whose NIC MTU could not be read, or is below network.mtu")["ansible.builtin.set_fact"]
    variables = {"_none_nic_mtu": {"results": RESULTS}, "_rack_parsed": {"summary": {"fabric_mtu": mtu}}}
    return {k: render(v, variables) for k, v in facts.items()}


def shown(name, mtu=9000):
    """The message of task name, or None when its when is false."""
    t = task(name)
    variables = dict(found(mtu), _rack_parsed={"summary": {"fabric_mtu": mtu}})
    if render("{{ %s }}" % t["when"], variables) not in (True, "True"):
        return None
    module = next(m for m in t if m.startswith("ansible.builtin."))
    return " ".join(render(t[module]["msg"], variables).split())


def test_unread_nodes_carry_their_reason():
    assert found()["_none_unread"] == ["n3 (no NIC holds its IP)", "n4 (/bin/sh: bash: not found)"]
    assert shown("Report nodes whose NIC MTU could not be read") == (
        "WARNING: network.mtu is not checked on n3 (no NIC holds its IP), n4 (/bin/sh: bash: not found).")


def test_a_nic_below_the_profile_mtu_stops_the_install():
    assert found()["_none_too_small"] == ["n1 ens259f0np0 (MTU 1500)"]
    stop = shown("Fail if a node's NIC carries less than network.mtu")
    assert "would not fit on these NICs: n1 ens259f0np0 (MTU 1500)." in stop and "at most the smallest MTU listed" in stop


def test_nics_at_or_above_the_profile_mtu_pass():
    assert found(1500)["_none_too_small"] == [] and shown("Fail if a node's NIC carries less than network.mtu", 1500) is None


@pytest.mark.parametrize("topology, runs", [("none", True), ("clos", False), ("l2", False), ("star", False)])
def test_only_topology_none_reads_the_nics(topology, runs):
    assert render("{{ %s }}" % _CHECK["when"], {"_rack_parsed": {"summary": {"topology": topology}}}) in (runs, str(runs))
    assert task("Read the MTU of the NIC with each node's IP")["become"] is False


@pytest.mark.skipif(shutil.which("ip") is None, reason="needs iproute2")
@pytest.mark.parametrize("ip, rc, stdout, stderr", [
    ("127.0.0.1", 0, "lo " + pathlib.Path("/sys/class/net/lo/mtu").read_text().strip() + "\n", ""),
    ("192.0.2.254", 1, "", "no NIC holds its IP\n"),
])
def test_the_probe_on_this_host(ip, rc, stdout, stderr):
    script = render(task("Read the MTU of the NIC with each node's IP")["ansible.builtin.shell"], {"item": node("n1", ip)})
    p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert (p.returncode, p.stdout, p.stderr) == (rc, stdout, stderr)
