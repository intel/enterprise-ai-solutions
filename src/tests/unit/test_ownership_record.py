# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
"""callback_plugins/ownership_record.py: what it records from task results."""
import importlib.util
import json
import pathlib

import pytest

_PLUGIN = pathlib.Path(__file__).resolve().parents[3] / "callback_plugins" / "ownership_record.py"
_spec = importlib.util.spec_from_file_location("ownership_record", _PLUGIN)
rec = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rec)


class Task:
    def __init__(self, action, args=None, name="task"):
        self.action, self.resolved_action, self.args, self._name = action, action, args or {}, name

    def get_name(self):
        return self._name


class Result:
    def __init__(self, task, result):
        self._task, self._result = task, result


class Display:
    def __init__(self):
        self.warnings = []

    def warning(self, msg):
        self.warnings.append(msg)


@pytest.fixture
def cb(tmp_path):
    cb = rec.CallbackModule()
    cb._display = Display()
    cb.file = tmp_path / "records.jsonl"
    cb.v2_runner_on_ok(Result(Task("ownership_k8s", {"action": "begin"}),
                              {"record_file": str(cb.file), "component": "app"}))
    return cb


def records(cb):
    if not cb.file.exists():
        return []
    return [json.loads(line) for line in cb.file.read_text().splitlines()]


def k8s_obj(kind, name, uid, namespace="app", api_version="v1"):
    return {"kind": kind, "apiVersion": api_version, "metadata": {"name": name, "namespace": namespace, "uid": uid}}


def test_k8s_result(cb):
    cb.v2_runner_on_ok(Result(Task("kubernetes.core.k8s"), {"result": k8s_obj("ConfigMap", "cm", "u1"),
                                                            "method": "create"}))
    assert records(cb) == [{"type": "object", "task": "task", "kind": "ConfigMap", "api_version": "v1",
                            "namespace": "app", "name": "cm", "uid": "u1", "method": "create", "component": "app"}]


def test_k8s_multi_document_result(cb):
    cb.v2_runner_on_ok(Result(Task("kubernetes.core.k8s"), {"result": {"results": [
        {"result": k8s_obj("ConfigMap", "a", "u1"), "method": "create"},
        {"result": k8s_obj("Secret", "b", "u2"), "method": "update"}]}}))
    assert [(r["name"], r["method"]) for r in records(cb)] == [("a", "create"), ("b", "update")]


def test_no_log_k8s_task_is_named_from_its_definition(cb):
    definition = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "creds"}}
    cb.v2_runner_on_ok(Result(Task("kubernetes.core.k8s", {"definition": definition, "namespace": "app"}),
                              {"censored": "hidden", "_ansible_no_log": True}))
    assert records(cb) == [{"type": "named", "task": "task", "kind": "Secret", "api_version": "v1",
                            "namespace": "app", "name": "creds", "component": "app"}]


def test_no_log_k8s_task_with_src_warns(cb):
    cb.v2_runner_on_ok(Result(Task("kubernetes.core.k8s", {"src": "secret.yaml"}, name="apply"),
                              {"censored": "hidden"}))
    assert records(cb) == []
    assert len(cb._display.warnings) == 1 and "'apply'" in cb._display.warnings[0]


def test_no_log_k8s_delete_by_name_does_not_warn(cb):
    cb.v2_runner_on_ok(Result(Task("kubernetes.core.k8s", {"kind": "Secret", "name": "x", "state": "absent"}),
                              {"censored": "hidden"}))
    assert records(cb) == [] and cb._display.warnings == []


def test_loop_records_each_item_once(cb):
    task = Task("kubernetes.core.k8s")
    items = [{"result": k8s_obj("ConfigMap", n, "u-" + n), "method": "create"} for n in ("a", "b")]
    for item in items:
        cb.v2_runner_item_on_ok(Result(task, item))
    cb.v2_runner_on_ok(Result(task, {"results": items, "changed": True}))  # the loop's summary
    assert [r["name"] for r in records(cb)] == ["a", "b"]


def test_failed_helm_task_still_records_the_release(cb):
    task = Task("kubernetes.core.helm", {"release_name": "web", "release_namespace": "app"})
    cb.v2_runner_on_failed(Result(task, {"failed": True, "msg": "timed out"}))
    assert records(cb) == [{"type": "release", "task": "task", "name": "web", "namespace": "app",
                            "component": "app"}]


def test_failed_task_writes_no_failed_record(cb):
    cb.v2_runner_on_failed(Result(Task("ansible.builtin.command", {}), {"failed": True, "cmd": "false"}))
    assert records(cb) == []


def test_helm_absent_is_not_recorded(cb):
    cb.v2_runner_on_ok(Result(Task("kubernetes.core.helm", {"name": "web", "namespace": "app", "state": "absent"}),
                              {"changed": True}))
    assert records(cb) == []


def test_kubectl_output(cb):
    out = "namespace/app created\ndeployment.apps/web configured\nservice/web unchanged\n" \
          "route.route.openshift.io/web created\nsomething else\n"
    cb.v2_runner_on_ok(Result(Task("ansible.builtin.shell"), {"cmd": "kubectl apply -n app -f -", "stdout": out}))
    assert [(r["resource"], r["name"], r["namespace"]) for r in records(cb)] == [
        ("namespace", "app", "app"), ("deployment.apps", "web", "app"), ("service", "web", "app"),
        ("route.route.openshift.io", "web", "app")]


def test_oc_new_project_and_helm_cli(cb):
    cb.v2_runner_on_ok(Result(Task("command"), {"cmd": ["oc", "new-project", "demo"],
                                                "stdout": 'Now using project "demo" on server "x".'}))
    cb.v2_runner_on_ok(Result(Task("command"), {"cmd": "helm upgrade --install web chart",
                                                "stdout": "Release \"web\" has been upgraded.\nNAME: web\nNAMESPACE: app\n"}))
    assert [(r["type"], r.get("resource"), r["name"]) for r in records(cb)] == [
        ("cli", "namespace", "demo"), ("release", None, "web")]


def test_nothing_recorded_after_label(cb):
    cb.v2_runner_on_ok(Result(Task("ownership_k8s", {"action": "label"}), {}))
    cb.v2_runner_on_ok(Result(Task("kubernetes.core.k8s"), {"result": k8s_obj("ConfigMap", "cm", "u1")}))
    assert records(cb) == []
