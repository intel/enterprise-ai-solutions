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

import copy
import json
import pathlib
import shutil
import subprocess
import sys

import pytest
import yaml

from plugin_loader import CLOS_PROFILE as CLOS, EXAMPLE_PROFILE, write_rack_inventory

INSTALLER = pathlib.Path(__file__).resolve().parents[3] / "es_auto_installer.sh"

pytestmark = pytest.mark.skipif(shutil.which("yq") is None, reason="the installer's helpers need yq v4")

PENDING = "ei.intel.com/add-node-pending"
CP_TAINT = "node-role.kubernetes.io/control-plane"

FAKE_KUBECTL = """#!/bin/bash
[[ -z "${KUBECTL_FAIL:-}" ]] || { echo "connection refused" >&2; exit 1; }
case "$*" in
    "get nodes -o json") cat "$FAKE/nodes.json" ;;
    "get nodes -o jsonpath="*) yq -p json -r '.items[].metadata.name' "$FAKE/nodes.json" ;;
    "get pods -A -o json") cat "$FAKE/pods.json" ;;
    "get namespaces -o jsonpath="*) cat "$FAKE/namespaces" ;;
    "get pdb -A -o json") cat "$FAKE/pdbs.json" ;;
    "get pods -n "*" --field-selector spec.nodeName="*" -o name") echo "$*" >> "$FAKE/pdb_queries"; cat "$FAKE/pdb_pods" ;;
    *) echo "unexpected: kubectl $*" >&2; exit 2 ;;
esac
"""


def node(name, ready=True, taints=()):
    return {"metadata": {"name": name},
            "spec": {"taints": [{"key": key, "effect": "NoSchedule"} for key in taints]},
            "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]}}


def crashing_pod(name, ns="default", owner=None):
    owners = [{"kind": "ReplicaSet", "name": owner}] if owner else []
    return {"metadata": {"name": name, "namespace": ns, "ownerReferences": owners},
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
            (tmp_path / "venv" / "bin").mkdir(parents=True)
            (tmp_path / "venv" / "bin" / "python3").symlink_to(sys.executable)
            self.config(storage_backend="nfs")
            self.inventory(["cp1"], ["w1"])
            self.live([])
            self.pods([])
            self.namespaces(["default", "kube-system"])
            self.pdbs([], pods_on_node=[])

        def declare(self, control_plane, workers=(), **top):
            nodes = {"nodes_control_plane": [{"ip": "10.0.0.%d" % i, "hostname": h}
                                             for i, h in enumerate(control_plane, 1)], **top}
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

        def rack(self, installed, edited):
            """A rack-scale env installed from one profile, then edited to another."""
            self.config(storage_backend="ceph", rack_scale_enabled=True)
            write_rack_inventory(copy.deepcopy(installed), self.env / "inventory")
            (self.env / "rack-profile.yaml").write_text(yaml.safe_dump(edited))

        def live(self, nodes):
            """Nodes by name ("cp*" carry the control-plane taint), or as built by node()."""
            (tmp_path / "nodes.json").write_text(json.dumps({"items": [
                n if isinstance(n, dict) else node(n, taints=[CP_TAINT] if n.startswith("cp") else ())
                for n in nodes]}))

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
                      f'ENV_INVENTORY_DIR="{self.env}/inventory" INVENTORY="{self.env}/inventory/hosts.yaml" '
                      f'VENV="{tmp_path / "venv"}" ACTION={action} '
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
    cluster.config(storage_backend="nfs")
    result = cluster.run("do_add_node")
    assert result.returncode == 0, result.stderr
    assert "resuming unfinished node(s)" in result.stdout
    assert "would run: scale.yml --limit=w1,kube_control_plane" in result.stdout


def test_add_node_refuses_to_resume_on_single_node_storage(cluster):
    # The resume runs scale.yml again; refused before it, not after.
    cluster.declare(["cp1"], ["w1"])
    cluster.live(["cp1", node("w1", taints=[PENDING])])
    cluster.config()
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "storage_backend is local-path" in result.stderr


def test_add_node_refuses_a_loopback_control_plane(cluster):
    cluster.declare(["cp1"], ["w1", "w2"])
    nodes = yaml.safe_load((cluster.env / "nodes.yaml").read_text())
    nodes["nodes_control_plane"][0]["ip"] = "127.0.0.1"
    (cluster.env / "nodes.yaml").write_text(yaml.safe_dump(nodes))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "cp1 is at a loopback address" in result.stderr


def test_add_node_refuses_to_end_the_control_planes_worker_role(cluster):
    cluster.declare(["cp1"], ["w1"])
    cluster.inventory(["cp1"])
    cluster.live([node("cp1")])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "the control plane (cp1) has no NoSchedule taint" in result.stderr
    cluster.declare(["cp1"], ["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 0, result.stderr
    # A tainted control plane is not a worker, whatever the inventory says.
    cluster.declare(["cp1"], ["w1"])
    cluster.live(["cp1"])
    assert cluster.run("do_add_node").returncode == 0


def test_add_node_refuses_upper_case_hostnames(cluster):
    cluster.declare(["cp1"], ["w1", "Worker2"])
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "upper-case letters: Worker2" in result.stderr


def test_add_node_refuses_kubelet_args_that_drop_the_pending_taint(cluster):
    cluster.declare(["cp1"], ["w1", "w2"])
    cluster.live(["cp1", "w1"])
    (cluster.env / "kubespray_extra.yml").write_text("kubelet_config_extra_args: {maxPods: 200}\n")
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "sets kubelet_config_extra_args" in result.stderr


@pytest.mark.parametrize("top, node, expected", [
    ({"nodes_ssh_key": "~/.ssh/k"}, {}, ["10.0.1.1", "", "~/.ssh/k"]),
    ({"nodes_ssh_user": "ops"}, {}, ["10.0.1.1", "ops", ""]),
    ({"nodes_ssh_user": "ops", "nodes_ssh_key": "~/.ssh/k"}, {"ssh_user": "root"}, ["10.0.1.1", "root", "~/.ssh/k"]),
])
def test_nodes_yaml_ssh_target_keeps_empty_fields(cluster, top, node, expected):
    cluster.declare(["cp1"], ["w1"], **top)
    nodes = yaml.safe_load((cluster.env / "nodes.yaml").read_text())
    nodes["nodes_workers"][0].update(node)
    (cluster.env / "nodes.yaml").write_text(yaml.safe_dump(nodes))
    result = cluster.run('{ read -r ip; read -r user; read -r key; } < <(_nodes_yaml_ssh_target w1) || true; '
                         'printf "%s|%s|%s" "$ip" "$user" "$key"')
    assert result.stdout.split("|") == expected


def test_kubespray_args(cluster):
    result = cluster.run('for a in install teardown scale remove; do _kubespray_args $a w1,w2; '
                         'echo "$(basename $KS_PB) ${KS_ARGS[*]}"; done; '
                         'NODE_LOST=true; _kubespray_args remove w1; echo "lost ${KS_ARGS[*]}"')
    lines = result.stdout.splitlines()
    assert lines[0] == "cluster.yml --flush-cache"
    assert lines[1] == 'reset.yml --flush-cache -e {"reset_confirmation": true}'
    assert lines[2].startswith("scale.yml --limit=w1,w2,kube_control_plane -e ")
    taint = json.loads(lines[2].split(" -e ", 1)[1])["kubelet_config_extra_args"]
    assert "'key': '%s', 'effect': 'NoSchedule'" % PENDING in taint and "('w1,w2' | split(','))" in taint
    assert lines[3] == 'remove-node.yml -e node=w1,w2 -e {"skip_confirmation": true}'
    assert lines[4] == ('lost -e node=w1 -e {"skip_confirmation": true} -e {"allow_ungraceful_removal": true} '
                        '-e {"drain_timeout": "60s"} -e {"reset_nodes": false}')


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


def test_remove_node_refuses_a_control_plane_node(cluster):
    cluster.declare(["cp1", "cp2"], ["w1"])
    cluster.inventory(["cp1", "cp2", "cp3"], ["w1"])
    cluster.live(["cp1", "cp2", "cp3", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 1
    assert "only removes worker nodes: cp3 is a control-plane node" in result.stderr


def test_remove_node_refuses_nodes_that_have_not_joined(cluster):
    cluster.declare(["cp1"], ["w3"])
    cluster.inventory(["cp1"], ["w1"])
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 1
    assert "lists node(s) that are not in the cluster: w3" in result.stderr


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

def pdb(name, allowed, selector):
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
    cluster.pdbs([pdb("app-pdb", 0, {"matchLabels": {"app": "x"}}), pdb("ok-pdb", 1, {"matchLabels": {"app": "y"}})],
                 pods_on_node=["x-1"])
    result = cluster.run("_pdb_blocked_pods w1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["default/x-1\tdefault/app-pdb\tselector=app=x"]


@pytest.mark.parametrize("selector, query", [
    ({"matchExpressions": [{"key": "app", "operator": "In", "values": ["x", "y"]},
                           {"key": "tier", "operator": "Exists"}]}, "-l app in (x,y),tier "),
    ({"matchLabels": {"app": "x"}, "matchExpressions": [{"key": "old", "operator": "DoesNotExist"}]}, "-l app=x,!old "),
    ({}, "get pods -n default --field-selector"),
])
def test_a_pdb_selector_lists_the_pods_it_covers(cluster, selector, query):
    cluster.pdbs([pdb("p", 0, selector)], pods_on_node=["x-1"])
    result = cluster.run("_pdb_blocked_pods w1")
    assert result.stdout.startswith("default/x-1\tdefault/p\t"), result.stdout
    assert query in (cluster.env.parent / "pdb_queries").read_text()


def test_a_pdb_without_a_selector_covers_no_pod(cluster):
    cluster.pdbs([{"metadata": {"name": "p", "namespace": "default"}, "spec": {}, "status": {"disruptionsAllowed": 0}}],
                 pods_on_node=["x-1"])
    assert cluster.run("_pdb_blocked_pods w1").stdout == ""


def test_pdbs_that_cannot_be_listed_fail_the_check(cluster):
    result = cluster.run("_pdb_blocked_pods w1", kubectl_fail=True)
    assert result.returncode == 1
    assert "connection refused" in result.stdout


# ---------------------------------------------------------------------------
# Rack-scale: the nodes come from rack-profile.yaml
# ---------------------------------------------------------------------------

def rack_node(name, ip, *roles, **extra):
    return name, {"mgmt_ip": "172.16.0.%d" % ip, "roles": list(roles),
                  "fabric_nics": [{"nic": "ens1", "fabric_ip": "10.201.1.%d" % ip, "leaf": "leaf1"}], **extra}


def edited(profile, add=(), remove=(), **network):
    out = copy.deepcopy(profile)
    nodes = out["racks"]["r1"]["nodes"]
    nodes.update(dict(add))
    for name in remove:
        del nodes[name]
    out["network"].update(network)
    return out


def test_rack_add_node_takes_the_new_worker_from_the_profile(cluster):
    cluster.rack(CLOS, edited(CLOS, add=[rack_node("w2", 3, "kube_node")]))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 0, result.stderr
    assert "would run: scale.yml --limit=w2,kube_control_plane" in result.stdout


def test_rack_add_node_refuses_an_edit_to_the_nodes_that_stay(cluster):
    cluster.rack(CLOS, edited(CLOS, add=[rack_node("w2", 3, "kube_node")], mtu=1500))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "also changes the nodes that stay" in result.stderr
    assert "w1: netplan or BGP peer files" in result.stderr


def test_rack_add_node_refuses_a_new_etcd_node(cluster):
    cluster.rack(CLOS, edited(CLOS, add=[rack_node("w2", 3, "kube_node", "etcd")]))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "New etcd or storage node(s): w2" in result.stderr


def test_rack_add_node_refuses_new_etcd_only_nodes(cluster):
    cluster.rack(CLOS, edited(CLOS, add=[rack_node("e1", 6, "etcd"), rack_node("e2", 7, "etcd")]))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "New etcd or storage node(s): e1, e2" in result.stderr


def test_rack_add_node_refuses_an_upper_case_node_name(cluster):
    cluster.rack(CLOS, edited(CLOS, add=[rack_node("W2", 3, "kube_node")]))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "rack-profile.yaml with upper-case letters: W2" in result.stderr


@pytest.mark.parametrize("action", ["add-node", "remove-node"])
def test_rack_refuses_a_deleted_etcd_only_node(cluster, action):
    etcd = edited(CLOS, add=[rack_node("e1", 6, "etcd"), rack_node("e2", 7, "etcd")])
    cluster.rack(etcd, edited(etcd, remove=["e1"]))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_" + action.replace("-", "_"), action=action)
    assert result.returncode == 1
    assert "e1 is an etcd or storage node" in result.stderr


def with_storage_worker(profile):
    out = edited(profile, add=[rack_node("s2", 8, "kube_node", "storage")])
    out["storage"]["nodes"].append({"name": "s2", "devices": ["nvme1n1"]})
    return out


def test_rack_add_node_refuses_a_new_storage_node_as_such(cluster):
    cluster.rack(CLOS, with_storage_worker(CLOS))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "New etcd or storage node(s): s2" in result.stderr


def test_rack_remove_node_refuses_a_storage_node(cluster):
    cluster.rack(with_storage_worker(CLOS), CLOS)
    cluster.live(["cp1", "w1", "s2"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 1
    assert "s2 is an etcd or storage node" in result.stderr


def test_rack_remove_node_refuses_a_control_plane_node(cluster):
    three = edited(CLOS, add=[rack_node("cp2", 4, "kube_control_plane", "etcd"),
                              rack_node("cp3", 5, "kube_control_plane", "etcd")])
    cluster.rack(three, edited(three, remove=["cp3"]))
    cluster.live(["cp1", "cp2", "cp3", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 1
    assert "cp3 is a control-plane node" in result.stderr


def test_rack_refuses_a_control_plane_node_gone_from_the_cluster_and_the_profile(cluster):
    three = edited(CLOS, add=[rack_node("cp2", 4, "kube_control_plane", "etcd"),
                              rack_node("cp3", 5, "kube_control_plane", "etcd")])
    cluster.rack(three, edited(three, remove=["cp3"]))
    cluster.live(["cp1", "cp2", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 1
    assert "cp3 is a control-plane node" in result.stderr


def test_rack_remove_node_targets_the_worker_deleted_from_the_profile(cluster):
    cluster.rack(CLOS, edited(CLOS, remove=["w1"]))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_remove_node", action="remove-node", node_lost=True)
    assert result.returncode == 1
    assert "still Ready, not lost: w1" in result.stderr


def test_rack_remove_node_leaves_etcd_only_nodes_alone(cluster):
    etcd = edited(CLOS, add=[rack_node("e1", 6, "etcd"), rack_node("e2", 7, "etcd")])
    cluster.rack(etcd, etcd)
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 0, result.stderr
    assert "nothing to do" in result.stdout


def test_rack_remove_node_finishes_an_unfinished_post_phase(cluster):
    two = edited(CLOS, add=[rack_node("w2", 3, "kube_node")])
    cluster.rack(two, edited(two, remove=["w2"]))
    cluster.live(["cp1", "w1"])
    result = cluster.run("do_remove_node", action="remove-node")
    assert result.returncode == 0, result.stderr
    assert "finishing the post phase for: w2" in result.stdout
    assert "removed with --node-lost, re-run with --node-lost" in result.stdout


def test_only_a_rack_runs_the_kubernetes_role_without_its_dependencies(cluster):
    call = 'v=(); _node_scaling_vars v; echo "${v[*]}"'
    assert cluster.run(call).stdout == "-e target=kubernetes -e _node_scaling_rack=false\n"
    cluster.rack(CLOS, CLOS)
    assert cluster.run(call).stdout == "-e target=kubernetes -e _node_scaling_rack=true -e _include_deps=false\n"


def test_rack_refuses_a_star_rack(cluster):
    cluster.rack(EXAMPLE_PROFILE, EXAMPLE_PROFILE)
    cluster.live(["master1", "master2", "worker1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "not supported on a star rack" in result.stderr


def test_rack_refuses_nodes_in_nodes_yaml(cluster):
    cluster.rack(CLOS, CLOS)
    cluster.declare(["cp1"], ["w1"])
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "takes them from rack-profile.yaml" in result.stderr


def test_rack_refuses_a_profile_that_does_not_parse(cluster):
    cluster.rack(CLOS, CLOS)
    (cluster.env / "rack-profile.yaml").write_text("racks: [1]\n")
    result = cluster.run("do_add_node")
    assert result.returncode == 1
    assert "cannot read env/t/rack-profile.yaml or its inventory (env/t/inventory)" in result.stderr
    assert "'racks' must be a mapping" in result.stderr


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


def test_health_findings_list_unschedulable_pods(cluster):
    stuck = {"metadata": {"name": "web-7c-q2", "namespace": "default",
                          "ownerReferences": [{"kind": "ReplicaSet", "name": "web-7c"}]},
             "status": {"phase": "Pending",
                        "conditions": [{"type": "PodScheduled", "status": "False", "reason": "Unschedulable"}]}}
    cluster.pods([stuck])
    assert cluster.run("_cluster_health_findings").stdout.strip() == "pod default/web-7c-* is Unschedulable"


def test_health_findings_name_a_controllers_pods_after_it(cluster):
    cluster.pods([crashing_pod("app-6b4f-x1", owner="app-6b4f"), crashing_pod("app-6b4f-z9", owner="app-6b4f")])
    result = cluster.run('KUBE_CFG="$FAKE/nodes.json"; do_health_check')
    assert result.returncode == 1
    assert result.stderr.count("pod default/app-6b4f-* is CrashLoopBackOff") == 1


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
