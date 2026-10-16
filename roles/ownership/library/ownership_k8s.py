# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible module — label what a component created, and delete by those labels.

  snapshot  record the UID of every object on the cluster in state_file
  label     label every object created since the last snapshot (or label), then
            record the cluster again, so the next component starts from here
  delete    delete every object that matches selector, in dependency order

An object is labelled when all of these hold:
  - its UID is not in state_file: it appeared while the component ran
  - it has no ownerReferences: its owner is labelled, and GC removes it
  - one of `managers` wrote it (managedFields): the installer's own tools, not
    an operator reconciling in parallel
  - it has no `<prefix>/component` label yet: the first creator keeps it

Lists use metadata-only responses (PartialObjectMetadataList), so a snapshot of a
large cluster is a few MB, not the full objects.
"""

import json
import os
import time

from ansible.module_utils.basic import AnsibleModule

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
except ImportError:  # reported by main()
    client = None

DOCUMENTATION = r"""
module: ownership_k8s
short_description: Label the objects a component created, and delete by label
options:
  action: {type: str, required: true, choices: [snapshot, label, delete]}
  state_file: {type: path, description: UIDs seen so far (snapshot, label)}
  env: {type: str, description: Env name, the <prefix>/env label (label, delete)}
  layer: {type: str, description: Layer, the <prefix>/layer label (label)}
  component: {type: str, description: Component, the <prefix>/component label (label, delete)}
  managers: {type: list, elements: str, description: Field managers that count as the installer}
  skip_resources: {type: list, elements: str, description: "resource or resource.group never listed"}
  label_prefix: {type: str, description: Prefix of the component label}
  timeout: {type: int, default: 600, description: Seconds to wait per delete phase}
"""

META_LIST = "application/json;as=PartialObjectMetadataList;g=meta.k8s.io;v=v1,application/json"

# Groups served by Kubernetes itself. Everything else is a custom resource, deleted
# first, while the operator that holds its finalizers still runs.
BUILTIN_GROUPS = {
    "", "apps", "batch", "autoscaling", "policy", "networking.k8s.io", "rbac.authorization.k8s.io",
    "storage.k8s.io", "scheduling.k8s.io", "coordination.k8s.io", "discovery.k8s.io", "node.k8s.io",
    "admissionregistration.k8s.io", "apiextensions.k8s.io", "apiregistration.k8s.io",
    "certificates.k8s.io", "flowcontrol.apiserver.k8s.io", "resource.k8s.io",
}

# Delete phases, in order. Webhooks and APIServices go before the workloads behind
# them, so the API never calls a backend that is already gone.
PHASES = ("custom", "webhooks", "namespaced", "namespaces", "cluster", "crds")


def _get(api, path, query=None, headers=None):
    resp = api.call_api(path, "GET", query_params=query or [], header_params=headers or {"Accept": "application/json"},
                        auth_settings=["BearerToken"], _preload_content=False, _return_http_data_only=True)
    return json.loads(resp.data)


def _discover(api, skip):
    """Every resource that can be listed, patched and deleted, once, at the first version that
    serves it: the group's preferred version, then the others. A resource can live in an
    older version only (serving.kserve.io: InferenceService in v1beta1, ServingRuntime in v1alpha1)."""
    versions = [("", "v1", "/api/v1")]
    for g in _get(api, "/apis").get("groups", []):
        preferred = g["preferredVersion"]["groupVersion"]
        for gv in [preferred] + [v["groupVersion"] for v in g.get("versions", []) if v["groupVersion"] != preferred]:
            versions.append((g["name"], gv.split("/")[1], "/apis/" + gv))
    found, seen = [], set()
    for group, version, base in versions:
        try:
            resources = _get(api, base).get("resources", [])
        except ApiException:
            continue  # an unavailable aggregated API
        for r in resources:
            name = r["name"]
            full = name + ("." + group if group else "")
            if "/" in name or name in skip or full in skip or full in seen \
                    or not {"list", "patch", "delete"} <= set(r.get("verbs", [])):
                continue
            seen.add(full)
            found.append({"group": group, "version": version, "base": base, "resource": name,
                          "kind": r["kind"], "namespaced": r["namespaced"]})
    return found


def _list(api, res, selector=None):
    items, cont = [], None
    while True:
        query = [("limit", 500)] + ([("continue", cont)] if cont else []) \
            + ([("labelSelector", selector)] if selector else [])
        try:
            page = _get(api, "%s/%s" % (res["base"], res["resource"]), query, {"Accept": META_LIST})
        except ApiException:
            return items  # e.g. 403 on a resource we may not read
        items += page.get("items") or []
        cont = page.get("metadata", {}).get("continue")
        if not cont:
            return items


def _path(res, meta):
    ns = "/namespaces/%s" % meta["namespace"] if res["namespaced"] and meta.get("namespace") else ""
    return "%s%s/%s/%s" % (res["base"], ns, res["resource"], meta["name"])


def _ref(res, meta):
    return "%s %s%s" % (res["kind"], meta["namespace"] + "/" if meta.get("namespace") else "", meta["name"])


def _all_objects(api, resources, selector=None):
    """uid -> (resource, metadata). OpenShift serves some objects under a second API as
    well (project.openshift.io Project for Namespace, authorization.openshift.io for RBAC):
    one entry per object, through the Kubernetes API, so it is deleted in its own phase."""
    seen = {}
    for res in resources:
        for item in _list(api, res, selector):
            uid = item["metadata"]["uid"]
            if uid not in seen or (res["group"] in BUILTIN_GROUPS and seen[uid][0]["group"] not in BUILTIN_GROUPS):
                seen[uid] = (res, item["metadata"])
    return seen


def _phase(res):
    if res["resource"] == "customresourcedefinitions":
        return "crds"
    if res["resource"] in ("validatingwebhookconfigurations", "mutatingwebhookconfigurations", "apiservices"):
        return "webhooks"
    if res["group"] not in BUILTIN_GROUPS:
        return "custom"
    if res["resource"] == "namespaces":
        return "namespaces"
    return "namespaced" if res["namespaced"] else "cluster"


def _write(path, uids):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"uids": sorted(uids) if uids is not None else None}, f)
    os.replace(tmp, path)


def snapshot(api, p):
    _write(p["state_file"], _all_objects(api, _discover(api, set(p["skip_resources"]))).keys())
    return {"changed": False}


def label(api, p, module):
    try:
        with open(p["state_file"]) as f:
            known = json.load(f).get("uids")
    except (OSError, ValueError):
        known = None
    objects = _all_objects(api, _discover(api, set(p["skip_resources"])))
    labelled = []
    if known is not None:  # no baseline (the cluster did not exist yet): label nothing
        prefix = p["label_prefix"]
        labels = {prefix + "/env": p["env"], prefix + "/layer": p["layer"], prefix + "/component": p["component"]}
        known, managers, key = set(known), set(p["managers"]), prefix + "/component"
        for uid, (res, meta) in sorted(objects.items(), key=lambda o: _ref(*o[1])):
            if uid in known or meta.get("ownerReferences") or key in (meta.get("labels") or {}):
                continue
            if not managers & {m.get("manager") for m in meta.get("managedFields") or []}:
                continue
            if module.check_mode:
                labelled.append(_ref(res, meta))
                continue
            try:
                api.call_api(_path(res, meta), "PATCH", header_params={"Content-Type": "application/merge-patch+json"},
                             body={"metadata": {"labels": labels}}, auth_settings=["BearerToken"],
                             _preload_content=False)
            except ApiException as e:
                if e.status != 404:  # 404: deleted since the list
                    raise
                continue
            labelled.append(_ref(res, meta))
    if not module.check_mode:
        _write(p["state_file"], objects.keys())
    return {"changed": bool(labelled), "labelled": labelled}


def _exists(api, res, meta):
    try:
        return _get(api, _path(res, meta))["metadata"]["uid"] == meta["uid"]
    except ApiException as e:
        if e.status == 404:
            return False
        raise


def delete(api, p, module):
    selector = "%s/env=%s,%s/component=%s" % (p["label_prefix"], p["env"], p["label_prefix"], p["component"])
    objects = _all_objects(api, _discover(api, set(p["skip_resources"])), selector)
    deleted = []
    for phase in PHASES:
        targets = sorted((t for t in objects.values() if _phase(t[0]) == phase), key=lambda t: _ref(*t))
        for res, meta in targets:
            if module.check_mode:
                deleted.append(_ref(res, meta))
                continue
            try:
                api.call_api(_path(res, meta), "DELETE", body={"propagationPolicy": "Background"},
                             header_params={"Content-Type": "application/json"}, auth_settings=["BearerToken"],
                             _preload_content=False)
                deleted.append(_ref(res, meta))
            except ApiException as e:
                if e.status != 404:
                    raise
        deadline = time.time() + p["timeout"]
        while targets and not module.check_mode:  # the next phase may need this one gone
            targets = [t for t in targets if _exists(api, *t)]
            if not targets or time.time() > deadline:
                break
            time.sleep(5)
        left = [_ref(*t) for t in targets]
        if left:  # stop: a later phase may hold the controller that clears the finalizers
            return {"changed": bool(deleted), "deleted": deleted, "left": left, "phase": phase}
    return {"changed": bool(deleted), "deleted": deleted, "left": []}


def main():
    module = AnsibleModule(
        argument_spec={
            "action": {"type": "str", "required": True, "choices": ["snapshot", "label", "delete"]},
            "state_file": {"type": "path"},
            "env": {"type": "str"},
            "layer": {"type": "str", "default": "unknown"},
            "component": {"type": "str"},
            "managers": {"type": "list", "elements": "str", "default": []},
            "skip_resources": {"type": "list", "elements": "str", "default": []},
            "label_prefix": {"type": "str", "default": "ai-solutions.intel.com"},
            "timeout": {"type": "int", "default": 600},
        },
        required_if=[("action", "snapshot", ["state_file"]), ("action", "label", ["state_file", "env", "component"]),
                     ("action", "delete", ["env", "component"])],
        supports_check_mode=True,
    )
    if client is None:
        module.fail_json(msg="the kubernetes Python client is required")
    p = module.params
    kubeconfig = os.environ.get("K8S_AUTH_KUBECONFIG") or os.environ.get("KUBECONFIG") or "~/.kube/config"
    if p["action"] != "delete" and not os.path.exists(os.path.expanduser(kubeconfig)):
        # Kubespray has not built the cluster yet: the next label call takes the baseline.
        _write(p["state_file"], None)
        module.exit_json(changed=False, reachable=False, labelled=[])
    try:
        cfg = client.Configuration()
        config.load_kube_config(config_file=kubeconfig, client_configuration=cfg)
        # The client reads http(s)_proxy from the environment but not no_proxy, so
        # set it the way kubernetes.core does.
        cfg.no_proxy = os.environ.get("K8S_AUTH_NO_PROXY") or os.environ.get("no_proxy") or os.environ.get("NO_PROXY")
        api = client.ApiClient(cfg)
        _get(api, "/version")
    except Exception as e:  # pylint: disable=broad-except
        module.fail_json(msg="cluster at %s not reachable: %s" % (kubeconfig, e))
    try:
        if p["action"] == "snapshot":
            module.exit_json(**snapshot(api, p))
        module.exit_json(**(label(api, p, module) if p["action"] == "label" else delete(api, p, module)))
    except ApiException as e:
        try:
            reason = json.loads(e.body).get("message", e.reason)
        except (TypeError, ValueError):
            reason = e.reason
        module.fail_json(msg="%s failed: %s %s" % (p["action"], e.status, reason))


if __name__ == "__main__":
    main()
