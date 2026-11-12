#!/usr/bin/env python
# -*- coding: utf-8 -*-
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
#
# Unit tests for es_auto_installer.sh add-node/remove-node: the checks that
# run before any Ansible. The installer is sourced in bash with a stand-in
# kubectl that serves hand-built Node/Pod lists, so no cluster is needed.
#
#   python3 -m pytest src/tests/unit

import json
import pathlib
import shutil
import subprocess

import pytest
import yaml

INSTALLER = pathlib.Path(__file__).resolve().parents[3] / "es_auto_installer.sh"

pytestmark = pytest.mark.skipif(shutil.which("yq") is None, reason="the installer's helpers need yq v4")

PENDING = "ei.intel.com/add-node-pending"

FAKE_KUBECTL = """#!/bin/bash
[[ -z "${KUBECTL_FAIL:-}" ]] || { echo "connection refused" >&2; exit 1; }
case "$*" in
    "get nodes -o json") cat "$FAKE/nodes.json" ;;
    "get nodes -o jsonpath="*) yq -p json -r '.items[].metadata.name' "$FAKE/nodes.json" ;;
    "get pods -A -o json") cat "$FAKE/pods.json" ;;
    "get namespaces -o jsonpath="*) cat "$FAKE/namespaces" ;;
    "get pdb -A -o json") cat "$FAKE/pdbs.json" ;;
    "get pods -n "*" -l "*" --field-selector spec.nodeName="*" -o name") cat "$FAKE/pdb_pods" ;;
    *) echo "unexpected: kubectl $*" >&2; exit 2 ;;
esac
"""


def node(name, ready=True, taints=()):
    return {"metadata": {"name": name},
            "spec": {"taints": [{"key": key, "effect": "NoSchedule"} for key in taints]},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


def crashing_pod(name, ns="default"):
    return {"metadata": {"name": name, "namespace": ns},
            "status": {"phase": "Running",
                       "containerStatuses": [{"state": {"waiting": {"reason": "CrashLoopBackOff"}}}]}}


@pytest.fixture
def cluster(tmp_path):
    """Builds an env (nodes.yaml, global_config.yaml, inventory) and a live
    cluster, then runs one installer function against them."""

    class Cluster:
        def __init__(self):
            self.env = tmp_path / "env"
            (self.env / "inventory").mkdir(parents=True)
            (tmp_path / "bin").mkdir()
            kubectl = tmp_path / "bin" / "kubectl"
            kubectl.write_text(FAKE_KUBECTL)
            kubectl.chmod(0o755)
            ssh = tmp_path / "bin" / "ssh"
            ssh.write_text("#!/bin/bash\nexit 0\n")
            ssh.chmod(0o755)
            self.config(storage_backend="nfs")
            self.live([])
            self.pods([])
            self.namespaces(["default", "kube-system"])
            self.pdbs([], pods_on_node=[])

        def declare(self, control_plane, workers=()):
            nodes = {"nodes_control_plane": [{"ip": "10.0.0.%d" % i, "hostname": h}
                                             for i, h in enumerate(control_plane, 1)]}
            if workers:
                nodes["nodes_workers"] = [{"ip": "10.0.1.%d" % i, "hostname": h}
                                          for i, h in enumerate(workers, 1)]
            (self.env / "nodes.yaml").write_text(yaml.safe_dump(nodes))

        def inventory(self, control_plane, workers=()):
            group = lambda names: {"hosts": {n: None for n in names}}
            (self.env / "inventory" / "hosts.yaml").write_text(yaml.safe_dump({"all": {
                "hosts": {n: {"ansible_host": "10.0.9.9"} for n in [*control_plane, *workers]},
                "children": {"kube_control_plane": group(control_plane), "etcd": group(control_plane),
                             "kube_node": group(workers or control_plane)}}}))

        def config(self, **values):
            (self.env / "global_config.yaml").write_text(yaml.safe_dump(values))

        def live(self, nodes):
            (tmp_path / "nodes.json").write_text(json.dumps(
                {"items": [n if isinstance(n, dict) else node(n) for n in nodes]}))

        def pods(self, pods):
            (tmp_path / "pods.json").write_text(json.dumps({"items": pods}))

        def namespaces(self, names):
            (tmp_path / "namespaces").write_text("".join(n + "\n" for n in names))

        def pdbs(self, pdbs, pods_on_node):
            (tmp_path / "pdbs.json").write_text(json.dumps({"items": pdbs}))
            (tmp_path / "pdb_pods").write_text("".join("pod/%s\n" % p for p in pods_on_node))

        def run(self, call, action="add-node", dry_run=True, node_lost=False, kubectl_fail=False):
            script = (f'source "{INSTALLER}"\n'
                      f'ENV_NAME=t ENV_DIR="{self.env}" GLOBAL_CONFIG="{self.env}/global_config.yaml" '
                      f'INVENTORY="{self.env}/inventory/hosts.yaml" ACTION={action} '
                      f'DRY_RUN={str(dry_run).lower()} NODE_LOST={str(node_lost).lower()} EXTRA_VARS=()\n'
                      f'sleep() {{ :; }}\n'
                      f'{call}\n')
            env = {"PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin:{pathlib.Path(shutil.which('yq')).parent}",
                   "HOME": str(tmp_path), "FAKE": str(tmp_path)}
            if kubectl_fail:
                env["KUBECTL_FAIL"] = "1"
            return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=60)

    return Cluster()


# ---------------------------------------------------------------------------
# add-node
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("config, message", [
    ({"storage_backend": "local-path"}, "storage_backend is local-path"),
    ({}, "storage_backend is local-path"),
    ({"storage_backend": "custom", "storage_custom_rwx": False}, "storage_custom_rwx false"),
])
def test_add_node_refuses_single_node_storage(cluster, config, message):
    cluster.declare(["cp1"], ["w1", "w2"])
    cluster.live(["cp1", "w1"])
    cluster.config(**config)
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert message in result.stderr


@pytest.mark.parametrize("config", [
    {"storage_backend": "nfs"},
    {"storage_backend": "custom"},
    {"storage_backend": "custom", "storage_custom_rwx": True},
])
def test_add_node_accepts_shared_storage(cluster, config):
    cluster.declare(["cp1"], ["w1", "w2"])
    cluster.live(["cp1", "w1"])
    cluster.config(**config)
    result = cluster.run("do_add_node")
    assert result.returncode == 0, result.stderr
    assert "would run: scale.yml --limit=w2,kube_control_plane" in result.stdout


def test_add_node_refuses_a_joined_node_missing_from_nodes_yaml(cluster):
    cluster.declare(["cp1"], ["w2"])
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "in the cluster but not in env/t/nodes.yaml: w1" in result.stderr


def test_add_node_refuses_a_new_control_plane_node(cluster):
    cluster.declare(["cp1", "cp2"], ["w1"])
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "New control-plane node(s) detected: cp2" in result.stderr


def test_add_node_resumes_a_node_left_tainted(cluster):
    cluster.declare(["cp1"], ["w1"])
    cluster.live(["cp1", node("w1", taints=[PENDING])])
    result = cluster.run("do_add_node")
    assert result.returncode == 0, result.stderr
    assert "resuming unfinished node(s)" in result.stdout
    assert "would run: add-node post phase only" in result.stdout


def test_add_node_with_nothing_to_add(cluster):
    cluster.declare(["cp1"], ["w1"])
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout


def test_add_node_refuses_when_the_cluster_cannot_be_read(cluster):
    cluster.declare(["cp1"], ["w1", "w2"])
    result = cluster.run("do_add_node", kubectl_fail=True)
    assert result.returncode == 1
    assert "cannot list the cluster's nodes" in result.stderr


# ---------------------------------------------------------------------------
# remove-node
# ---------------------------------------------------------------------------

def test_remove_node_refuses_a_node_missing_from_the_inventory(cluster):
    cluster.declare(["cp1"], ["w1"])
    cluster.inventory(["cp1"], ["w1"])
    cluster.live(["cp1", "w1", "w2"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 1
    assert "w2 is in the cluster but no longer in" in result.stderr


def test_remove_node_refuses_the_first_control_plane_node(cluster):
    cluster.declare(["cp2", "cp3"], ["w1"])
    cluster.inventory(["cp1", "cp2", "cp3"], ["w1"])
    cluster.live(["cp1", "cp2", "cp3", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 1
    assert "cannot remove 'cp1'" in result.stderr


def test_remove_node_refuses_an_even_control_plane_count(cluster):
    cluster.declare(["cp1", "cp2"], ["w1"])
    cluster.inventory(["cp1", "cp2", "cp3"], ["w1"])
    cluster.live(["cp1", "cp2", "cp3", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 1
    assert "would leave 2 control-plane/etcd node(s)" in result.stderr


def test_remove_node_lost_refuses_a_ready_node(cluster):
    cluster.declare(["cp1"], ["w1"])
    cluster.inventory(["cp1"], ["w1", "w2"])
    cluster.live(["cp1", "w1", "w2"])
    result = cluster.run("do_remove_node", action="remove-node", node_lost=True)
    assert result.returncode == 1
    assert "still Ready, not lost: w2" in result.stderr


def test_remove_node_finishes_an_unfinished_post_phase(cluster):
    cluster.declare(["cp1"], ["w1"])
    cluster.inventory(["cp1"], ["w1", "w2"])
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 0, result.stderr
    assert "finishing the post phase for: w2" in result.stdout
    assert "would run: remove-node post phase only" in result.stdout


def test_remove_node_with_nothing_to_remove(cluster):
    cluster.declare(["cp1"], ["w1"])
    cluster.inventory(["cp1"], ["w1"])
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout


# ---------------------------------------------------------------------------
# Guards: ERAG, PodDisruptionBudgets
# ---------------------------------------------------------------------------

def pdb(name, allowed, match_labels=None):
    selector = {"matchLabels": match_labels} if match_labels else {"matchExpressions": [{"key": "app", "operator": "Exists"}]}
    return {"metadata": {"name": name, "namespace": "default"}, "spec": {"selector": selector},
            "status": {"disruptionsAllowed": allowed}}


def test_erag_namespaces_are_found(cluster):
    cluster.namespaces(["default", "edp", "rag-ui", "seaweedfs"])
    assert cluster.run("_erag_namespaces_present").stdout.split() == ["rag-ui", "edp"]


def test_erag_check_fails_closed(cluster):
    result = cluster.run("_erag_namespaces_present", kubectl_fail=True)
    assert result.returncode == 1
    assert "cannot list namespaces" in result.stderr


def test_a_pdb_with_no_disruption_left_blocks_its_pods(cluster):
    cluster.pdbs([pdb("app-pdb", 0, {"app": "x"}), pdb("ok-pdb", 1, {"app": "y"})], pods_on_node=["x-1"])
    result = cluster.run("_pdb_blocked_pods w1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["default/x-1\tdefault/app-pdb\tselector=app=x"]


def test_a_pdb_without_match_labels_is_reported(cluster):
    cluster.pdbs([pdb("expr-pdb", 0)], pods_on_node=[])
    result = cluster.run("_pdb_blocked_pods w1")
    assert "unsupported selector" in result.stdout


def test_pdbs_that_cannot_be_listed_fail_the_check(cluster):
    result = cluster.run("_pdb_blocked_pods w1", kubectl_fail=True)
    assert result.returncode == 1
    assert "connection refused" in result.stdout


# ---------------------------------------------------------------------------
# Health checks, rack-scale switch
# ---------------------------------------------------------------------------

def test_health_findings_fail_closed(cluster):
    result = cluster.run("_cluster_health_findings", kubectl_fail=True)
    assert result.stdout.strip() == "cannot list the cluster's nodes (kubectl get nodes failed)"


def test_health_findings_list_not_ready_nodes_and_crashing_pods(cluster):
    cluster.live(["cp1", node("w1", ready=False)])
    cluster.pods([crashing_pod("app-1")])
    result = cluster.run("_cluster_health_findings")
    assert result.stdout.split("\n")[:2] == ["node w1 is NotReady", "pod default/app-1 is CrashLoopBackOff"]


def test_health_regressions_ignore_what_was_already_wrong(cluster):
    cluster.live(["cp1", "w1"])
    cluster.pods([crashing_pod("app-1")])
    result = cluster.run('_health_regressions "pod default/app-1 is CrashLoopBackOff"')
    assert result.returncode == 0, result.stdout


def test_health_regressions_report_a_new_finding(cluster):
    cluster.live(["cp1", "w1"])
    cluster.pods([crashing_pod("app-1"), crashing_pod("app-2")])
    result = cluster.run('_health_regressions "pod default/app-1 is CrashLoopBackOff"')
    assert result.returncode == 1
    assert result.stdout.strip() == "pod default/app-2 is CrashLoopBackOff"


@pytest.mark.parametrize("value, enabled", [
    ("true", True), ("True", True), ("yes", True), ("on", True), ("1", True),
    ("false", False), ("no", False), ('""', False), (None, False),
])
def test_rack_scale_enabled(cluster, value, enabled):
    text = "storage_backend: nfs\n" + ("" if value is None else f"rack_scale_enabled: {value}\n")
    (cluster.env / "global_config.yaml").write_text(text)
    assert (cluster.run("_rack_scale_enabled").returncode == 0) is enabled
