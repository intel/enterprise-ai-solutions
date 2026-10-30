# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Callback — record which objects each component's tasks created or changed.

The ownership role's `begin` returns the component that runs and the file to
record into; from then until its `label`, every task result that names a
Kubernetes object is appended to that file as one JSON line:

  object    kubernetes.core.k8s / k8s_json_patch: kind, namespace, name, uid, method
  named     the same task with no_log (its result is censored): kind, namespace and
            name from the task's `definition`
  release   kubernetes.core.helm: release name and namespace, also when it failed
  cli       command or shell: each object its output reports the way kubectl does
            (`kind.group/name created`), which covers kubectl, oc and model-manager;
            the release `helm install` / `upgrade` prints
  failed    a task of the component failed (not ignored)

`label` then labels what these name, so an object another user or controller
creates while the installer runs is never taken for ours.
"""

import json
import re

import yaml

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = """
name: ownership_record
type: aggregate
short_description: record the objects each component's tasks create, for the ownership role
description:
  - Writes one JSON line per object a task result names, into the file the ownership role's begin returns.
"""

K8S = ("kubernetes.core.k8s", "kubernetes.core.k8s_json_patch")
HELM = ("kubernetes.core.helm",)
CLI = ("ansible.builtin.command", "ansible.builtin.shell", "command", "shell")
OWNERSHIP = ("ownership_k8s", "ansible.legacy.ownership_k8s")

HELM_WRITE = re.compile(r"\bhelm\b.*\b(install|upgrade)\b")
CLI_OBJECT = re.compile(r"^\s*([a-z0-9.-]+)/([a-z0-9.-]+) (created|configured|unchanged|serverside-applied|labeled|"
                        r"annotated|patched)$", re.M)
CLI_NAMESPACE = re.compile(r"(?:\s-n\s+|\s--namespace[= ])(\S+)")
CLI_PROJECT = re.compile(r'^Now using project "([^"]+)"', re.M)
HELM_NAME = re.compile(r"^NAME: (\S+)$", re.M)
HELM_NAMESPACE = re.compile(r"^NAMESPACE: (\S+)$", re.M)


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "ownership_record"
    CALLBACK_NEEDS_ENABLED = False  # always on: the ownership role depends on it

    def __init__(self):
        super().__init__()
        self._file = None
        self._component = None

    def _emit(self, **record):
        if not self._file:
            return
        record["component"] = self._component
        with open(self._file, "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")

    @staticmethod
    def _action(task):
        return getattr(task, "resolved_action", None) or task.action

    def _ownership(self, args, r):
        """`begin` starts recording for a component, `label` stops it."""
        if args.get("action") == "begin" and r.get("record_file"):
            self._file, self._component = r["record_file"], r.get("component")
        elif args.get("action") == "label":
            self._file = self._component = None

    def _named(self, task, args):
        """no_log k8s task: the object it applies, from its (templated) definition."""
        definition = args.get("definition")
        if isinstance(definition, str):
            try:
                docs = list(yaml.safe_load_all(definition))
            except yaml.YAMLError:
                docs = []
        else:
            docs = definition if isinstance(definition, list) else [definition]
        for doc in docs:
            if isinstance(doc, dict) and doc.get("kind") and (doc.get("metadata") or {}).get("name"):
                meta = doc["metadata"]
                self._emit(type="named", task=task, kind=doc["kind"], api_version=doc.get("apiVersion"),
                           namespace=meta.get("namespace") or args.get("namespace"), name=meta["name"])

    def _k8s(self, task, r):
        res = r.get("result") or {}
        docs = res.get("results") if isinstance(res, dict) and "results" in res else [res]
        for doc in docs or []:
            if not isinstance(doc, dict):
                continue
            method = doc.get("method") or r.get("method")
            obj = doc.get("result", doc)
            meta = (obj.get("metadata") or {}) if isinstance(obj, dict) else {}
            if meta.get("uid"):
                self._emit(type="object", task=task, kind=obj.get("kind"), api_version=obj.get("apiVersion"),
                           namespace=meta.get("namespace"), name=meta.get("name"), uid=meta["uid"], method=method)

    def _helm(self, task, args, r):
        if args.get("state") == "absent":
            return
        status = r.get("status") or {}
        name = status.get("name") or args.get("release_name") or args.get("name")
        namespace = status.get("namespace") or args.get("release_namespace") or args.get("namespace")
        if name and namespace and (r.get("changed") or status or r.get("failed")):
            self._emit(type="release", task=task, name=name, namespace=namespace)

    def _cli(self, task, r):
        cmd = r.get("cmd")
        cmd = " ".join(cmd) if isinstance(cmd, list) else (cmd or "")
        out = r.get("stdout") or ""
        ns = CLI_NAMESPACE.search(cmd)
        for resource, name, _ in CLI_OBJECT.findall(out):
            self._emit(type="cli", task=task, resource=resource, name=name, namespace=ns.group(1) if ns else None)
        for name in CLI_PROJECT.findall(out):
            self._emit(type="cli", task=task, resource="namespace", name=name, namespace=None)
        if HELM_WRITE.search(cmd) and HELM_NAME.search(out) and HELM_NAMESPACE.search(out):
            self._emit(type="release", task=task, name=HELM_NAME.search(out).group(1),
                       namespace=HELM_NAMESPACE.search(out).group(1))

    def _result(self, result, failed=False):
        task, r = result._task, result._result
        action, args = self._action(task), task.args or {}
        if action in OWNERSHIP:
            self._ownership(args, r)
            return
        if not self._file:
            return
        name = task.get_name()
        if r.get("censored") or r.get("_ansible_no_log"):
            if action == "kubernetes.core.k8s":
                self._named(name, args)
            elif action in HELM:
                self._helm(name, args, {"failed": failed})
        elif action in K8S:
            self._k8s(name, r)
        elif action in HELM:
            self._helm(name, args, dict(r, failed=failed or r.get("failed")))
        elif action in CLI:
            self._cli(name, r)

    def v2_runner_on_ok(self, result):
        if not isinstance(result._result.get("results"), list):  # a loop: see v2_runner_item_on_ok
            self._result(result)

    def v2_runner_item_on_ok(self, result):
        self._result(result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        if not isinstance(result._result.get("results"), list):
            self._result(result, failed=True)
        if not ignore_errors and self._file:
            self._emit(type="failed", task=result._task.get_name())

    def v2_runner_item_on_failed(self, result):
        self._result(result, failed=True)
