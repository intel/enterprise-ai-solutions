# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible module — label what a component created, and delete by those labels.

  snapshot  record the UID of every object on the cluster in state_file. When a
            run was killed while a component ran (see begin), first label what
            that component created
  begin     note in state_file which component runs now, and return the file the
            ownership_record callback records the component's task results into
  label     label what the component's tasks created, then record the cluster
            again, so the next component starts from here
  delete    delete every object that matches selector, in dependency order

What a component created comes from its own task results, never from what
appeared on the cluster meanwhile: other users, CronJobs and operators keep
writing while the installer runs. The ownership_record callback records every
object a kubernetes.core.k8s task returned, every release a kubernetes.core.helm
task installed, and every object kubectl/oc reported. An object is labelled when
  - a record names it, `declared` describes it (objects the component's Jobs or
    controllers create), or it belongs to one of the component's Helm releases
  - its UID is not in state_file, so the component created it, not changed it;
    or it is carried for this component (see below)
  - it has no ownerReferences: its owner is labelled, and GC removes it
  - it has no `<prefix>/component` label yet: the first creator keeps it

A Helm release is the component's when one of its tasks installed it and its
first release secret (v1) is new, or one release secret already carries the
component's labels. Then all of it is labelled: the objects of its manifest and
hooks, the objects annotated with it (meta.helm.sh/release-name and -namespace),
its release secrets, and the CRDs of the chart's crds/ that are new.

A component that failed, or a run that was killed inside it, may have created
objects no result reports. The new objects nobody claimed are carried for that
component in state_file: they count as new for it, and only for it, until it runs
again without a failure. Its rerun reports them (as updated) and labels them.

Labels a task adds to an object that was already there (a Namespace it does not
own gets istio.io/dataplane-mode, a Node a placement label) are recorded instead,
in the annotation <prefix>/labels-before.<env>.<component> on that object: each key
with the value it had before (null: absent) and the value set. Teardown restores
the keys that still hold the value set. Only `restore_resources` are followed
(Namespaces and Nodes by default), only objects a record names, and only objects
whose labels the state file holds.

Teardown also deletes what a labelled object creates without an owner reference:
the PersistentVolumeClaims of a StatefulSet's volumeClaimTemplates, and the Secret
cert-manager issues for a Certificate.

Lists use metadata-only responses (PartialObjectMetadataList), so a snapshot of a
large cluster is a few MB, not the full objects.
"""

import base64
import gzip
import json
import os
import re
import time

import yaml

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
  action: {type: str, required: true, choices: [snapshot, begin, label, delete]}
  state_file: {type: path, description: UIDs seen so far (snapshot, label)}
  env: {type: str, description: Env name, the <prefix>/env label (label, delete)}
  layer: {type: str, description: Layer, the <prefix>/layer label (label)}
  component: {type: str, description: Component, the <prefix>/component label (label, delete)}
  declared:
    type: dict
    description: "component -> objects it creates outside its tasks: [{kind, namespace, name, labels}], any subset"
  skip_resources: {type: list, elements: str, description: "resource or resource.group never listed"}
  label_prefix: {type: str, description: Prefix of the component label}
  restore_resources: {type: list, elements: str, description: "resources whose added labels teardown restores"}
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


def _read(path):
    """uids: None when there is no baseline yet; pending: the component a run was in
    when it stopped, or None; labels: uid -> labels of the objects in
    restore_resources; carry: component -> UIDs it may still claim (see label)."""
    try:
        with open(path) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    return {"uids": state.get("uids"), "pending": state.get("pending"), "labels": state.get("labels") or {},
            "carry": state.get("carry") or {}}


def _write(path, uids, pending=None, labels=None, carry=None):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"uids": sorted(uids) if uids is not None else None, "pending": pending,
                   "labels": labels or {}, "carry": {c: sorted(u) for c, u in (carry or {}).items() if u}}, f)
    os.replace(tmp, path)


def _record_file(state_file):
    """Where the ownership_record callback writes the task results."""
    return os.path.splitext(state_file)[0] + "-records.jsonl"


def _records(path, component, module):
    """The records of component, removed from the file; the others stay."""
    try:
        with open(path) as f:
            lines = [json.loads(line) for line in f if line.strip()]
    except (OSError, ValueError):
        return []
    mine = [r for r in lines if r.get("component") == component]
    if mine and not module.check_mode:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            for r in lines:
                if r.get("component") != component:
                    f.write(json.dumps(r, sort_keys=True) + "\n")
        os.replace(tmp, path)
    return mine


def _restorable(objects, p):
    """uid -> labels of every object in restore_resources."""
    wanted = set(p["restore_resources"])
    return {uid: meta.get("labels") or {} for uid, (res, meta) in objects.items()
            if res["resource"] in wanted or "%s.%s" % (res["resource"], res["group"]) in wanted}


def _before_key(p):
    """<prefix>/labels-before.<env>.<component>, a valid annotation name (63 chars)."""
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", "labels-before.%s.%s" % (p["env"], p["component"]))[:63]
    return "%s/%s" % (p["label_prefix"], name.strip("-_."))


def _patch(api, res, meta, body):
    try:
        api.call_api(_path(res, meta), "PATCH", header_params={"Content-Type": "application/merge-patch+json"},
                     body={"metadata": body}, auth_settings=["BearerToken"], _preload_content=False)
    except ApiException as e:
        if e.status != 404:  # 404: deleted since the list
            raise
        return False
    return True


def _record_added(api, objects, known, before, reported, p, module):
    """Record, on each object that was already there and that a task of the component
    changed, the labels added or changed since the last snapshot. Returns the refs recorded."""
    prefix, key = p["label_prefix"] + "/", _before_key(p)
    recorded = []
    for uid, labels in sorted(_restorable(objects, p).items(), key=lambda o: _ref(*objects[o[0]])):
        res, meta = objects[uid]
        if uid not in known or uid not in before or uid not in reported or prefix + "component" in labels:
            continue  # new (labelled instead), no recorded labels, not ours to change, or ours: deleted whole
        old = before[uid]
        added = {k: [old.get(k), v] for k, v in labels.items() if old.get(k) != v and not k.startswith(prefix)}
        if not added:
            continue
        try:
            record = json.loads((meta.get("annotations") or {}).get(key) or "{}")
        except ValueError:
            record = {}
        # A key already recorded keeps its first value before; it now holds the new value set.
        record = dict(added, **{k: [v[0], added[k][1] if k in added else v[1]] for k, v in record.items()})
        if module.check_mode or _patch(api, res, meta, {"annotations": {key: json.dumps(record, sort_keys=True)}}):
            recorded.append("%s: %s" % (_ref(res, meta), ", ".join(sorted(added))))
    return recorded


def _restore(api, resources, p, module):
    """Put back the labels recorded by _record_added for this env and component."""
    key, restored = _before_key(p), []
    for res in resources:
        if res["resource"] not in p["restore_resources"] \
                and "%s.%s" % (res["resource"], res["group"]) not in p["restore_resources"]:
            continue
        for meta in (item["metadata"] for item in _list(api, res)):
            raw = (meta.get("annotations") or {}).get(key)
            if raw is None:
                continue
            try:
                record = json.loads(raw)
            except ValueError:
                record = {}
            labels = meta.get("labels") or {}
            # Keys changed again since by someone else are left as they are.
            back = {k: v[0] for k, v in record.items() if labels.get(k) == v[1]}
            if module.check_mode or _patch(api, res, meta, {"labels": back, "annotations": {key: None}}):
                restored.append("%s: %s" % (_ref(res, meta), ", ".join(sorted(back)) or "(nothing left to restore)"))
    return restored


def snapshot(api, p, module):
    state = _read(p["state_file"])
    if state["pending"] and state["uids"] is not None:
        # The last run stopped inside a component, before it was labelled: label what
        # its tasks recorded, and carry what else appeared for its rerun.
        pending = state["pending"]
        result = label(api, dict(p, **pending), module, stopped=True)
        return {"changed": result["changed"], "recovered": pending, "labelled": result["labelled"],
                "recorded": result["recorded"]}
    if not module.check_mode:
        objects = _all_objects(api, _discover(api, set(p["skip_resources"])))
        carry = {c: set(u) & objects.keys() for c, u in state["carry"].items()}
        _write(p["state_file"], objects.keys(), labels=_restorable(objects, p), carry=carry)
    return {"changed": False, "labelled": [], "recorded": []}


def begin(p, module):
    state = _read(p["state_file"])
    if not module.check_mode:
        _write(p["state_file"], state["uids"], {"env": p["env"], "layer": p["layer"], "component": p["component"]},
               state["labels"], state["carry"])
    return {"changed": False, "component": p["component"], "record_file": _record_file(p["state_file"])}


def _helm_release(api, secret):
    """The decoded release of a Helm release secret (base64, base64, gzip, JSON)."""
    data = _get(api, "/api/v1/namespaces/%s/secrets/%s" % (secret["namespace"], secret["name"]))
    raw = base64.b64decode(base64.b64decode((data.get("data") or {}).get("release") or ""))
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def _manifest_refs(text, namespace):
    """(kind, namespace or None, name) of each object in a rendered manifest."""
    refs = []
    for doc in yaml.safe_load_all(text or ""):
        if isinstance(doc, dict) and doc.get("kind") and (doc.get("metadata") or {}).get("name"):
            refs.append((doc["kind"], doc["metadata"].get("namespace") or namespace, doc["metadata"]["name"]))
    return refs


def _helm_claims(api, objects, index, known, new, reported, p):
    """(claimed, crds): UIDs of the component's Helm releases: release secrets, the objects
    of their manifest and hooks, the objects annotated with them; and the CRDs of their
    charts' crds/, which are only the component's when new."""
    prefix = p["label_prefix"]
    releases, first, latest = {}, {}, {}
    for uid, (res, meta) in objects.items():
        labels = meta.get("labels") or {}
        if res["group"] == "" and res["resource"] == "secrets" and labels.get("owner") == "helm" and labels.get("name"):
            release = (meta.get("namespace"), labels["name"])
            if release not in reported:
                continue
            releases.setdefault(release, []).append(uid)
            version = int(labels.get("version") or 0)
            if version == 1:
                first[release] = uid
            if version >= latest.get(release, (0, None))[0]:
                latest[release] = (version, meta)
    ours = {release for release, uids in releases.items()
            if (release in first and new(first[release])) or any(
                (objects[u][1].get("labels") or {}).get(prefix + "/env") == p["env"]
                and (objects[u][1].get("labels") or {}).get(prefix + "/component") == p["component"] for u in uids)}
    claimed, crds = {u for release in ours for u in releases[release]}, set()
    for release in ours:
        try:
            rel = _helm_release(api, latest[release][1])
        except (ApiException, ValueError, TypeError, OSError):
            continue  # unreadable release: the annotations below still hold
        refs = _manifest_refs(rel.get("manifest"), release[0])
        for hook in rel.get("hooks") or []:
            refs += _manifest_refs(hook.get("manifest"), release[0])
        for ref in refs:
            claimed |= index(*ref)
        for f in (rel.get("chart") or {}).get("files") or []:
            if (f.get("name") or "").startswith("crds/"):
                try:
                    text = base64.b64decode(f.get("data") or "").decode()
                except (ValueError, UnicodeDecodeError):
                    continue
                for ref in _manifest_refs(text, None):
                    crds |= index(*ref)
    for uid, (res, meta) in objects.items():
        annotations = meta.get("annotations") or {}
        if (annotations.get("meta.helm.sh/release-namespace"), annotations.get("meta.helm.sh/release-name")) in ours:
            claimed.add(uid)
    return claimed, crds


def _index(objects):
    """find(kind, namespace, name) -> UIDs. kind is a Kind (Deployment) or, as kubectl
    prints it, a lowercase resource with its group (deployment.apps); namespace None
    matches any namespace, and is ignored for cluster-scoped objects."""
    by_name = {}
    for uid, (res, meta) in objects.items():
        by_name.setdefault(meta["name"], []).append((uid, res, meta))

    def find(kind, namespace, name):
        kind, _, group = (kind or "").partition(".")
        found = set()
        for uid, res, meta in by_name.get(name, []):
            if kind.lower() != res["kind"].lower():
                continue
            if group and group != res["group"]:
                continue
            if res["namespaced"] and namespace and meta.get("namespace") != namespace:
                continue
            found.add(uid)
        return found
    return find


def _declared(objects, declared):
    """UIDs of the objects `declared` describes."""
    found = set()
    for d in declared or []:
        for uid, (res, meta) in objects.items():
            if d.get("kind") and d["kind"] != res["kind"]:
                continue
            if d.get("namespace") and d["namespace"] != meta.get("namespace"):
                continue
            if d.get("name") and d["name"] != meta["name"]:
                continue
            labels = meta.get("labels") or {}
            if any(labels.get(k) != str(v) for k, v in (d.get("labels") or {}).items()):
                continue
            found.add(uid)
    return found


def label(api, p, module, stopped=False):
    state = _read(p["state_file"])
    known, before, carry = state["uids"], state["labels"], {c: set(u) for c, u in state["carry"].items()}
    objects = _all_objects(api, _discover(api, set(p["skip_resources"])))
    records = _records(_record_file(p["state_file"]), p["component"], module)
    labelled, recorded = [], []
    if known is not None:  # no baseline (the cluster did not exist yet): label nothing
        prefix = p["label_prefix"]
        labels = {prefix + "/env": p["env"], prefix + "/layer": p["layer"], prefix + "/component": p["component"]}
        known, key = set(known), prefix + "/component"
        mine = carry.get(p["component"], set())

        def new(uid):
            return uid not in known or uid in mine

        find = _index(objects)
        reported, releases = set(), set()
        for r in records:
            if r["type"] == "object":
                reported |= {r["uid"]} & objects.keys()
            elif r["type"] == "named":
                reported |= find(r["kind"], r.get("namespace"), r["name"])
            elif r["type"] == "cli":
                reported |= find(r["resource"], r.get("namespace"), r["name"])
            elif r["type"] == "release":
                releases.add((r["namespace"], r["name"]))
        helm, crds = _helm_claims(api, objects, find, known, new, releases, p)
        declared = _declared(objects, (p["declared"] or {}).get(p["component"]))
        claimed = helm | {u for u in reported | crds | declared if new(u)}
        for uid in sorted(claimed, key=lambda u: _ref(*objects[u])):
            res, meta = objects[uid]
            if meta.get("ownerReferences") or key in (meta.get("labels") or {}):
                continue
            if module.check_mode or _patch(api, res, meta, {"labels": labels}):
                labelled.append(_ref(res, meta))
        recorded = _record_added(api, objects, known, before, reported, p, module)
        # Carry what appeared and nobody claimed when the component failed or the run
        # stopped inside it: its rerun may report those objects, as updated.
        failed = stopped or any(r["type"] == "failed" for r in records)
        unclaimed = {u for u in objects if new(u) and u not in claimed}
        carry[p["component"]] = (mine | unclaimed) - claimed if failed else set()
    if not module.check_mode:
        carry = {c: u & objects.keys() for c, u in carry.items()}
        _write(p["state_file"], objects.keys(), labels=_restorable(objects, p), carry=carry)
    return {"changed": bool(labelled or recorded), "labelled": labelled, "recorded": recorded,
            "records": len(records)}


def _exists(api, res, meta):
    try:
        return _get(api, _path(res, meta))["metadata"]["uid"] == meta["uid"]
    except ApiException as e:
        if e.status == 404:
            return False
        raise


def _created_by(api, resources, objects, p):
    """uid -> (resource, metadata) of what labelled objects create without an owner
    reference: the PVCs of a StatefulSet's volumeClaimTemplates, the Secret of a
    cert-manager Certificate. One another component labelled is left to it."""
    by_kind = {(r["group"], r["resource"]): r for r in resources}
    pvcs, secrets = by_kind.get(("", "persistentvolumeclaims")), by_kind.get(("", "secrets"))
    key, found = p["label_prefix"] + "/component", {}

    def add(res, namespace, match):
        for item in _list(api, dict(res, base="%s/namespaces/%s" % (res["base"], namespace))):
            meta = item["metadata"]
            if match(meta) and (meta.get("labels") or {}).get(key, p["component"]) == p["component"]:
                found[meta["uid"]] = (res, meta)

    def spec(res, meta):
        try:
            return _get(api, _path(res, meta)).get("spec") or {}
        except ApiException as e:
            if e.status == 404:
                return {}
            raise

    for res, meta in list(objects.values()):
        if res["resource"] == "statefulsets" and res["group"] == "apps" and pvcs:
            spec_ = spec(res, meta)
            names = [t["metadata"]["name"] for t in spec_.get("volumeClaimTemplates") or []]
            pattern = re.compile("^(%s)-%s-[0-9]+$" % ("|".join(map(re.escape, names)), re.escape(meta["name"])))
            if names:
                add(pvcs, meta["namespace"], lambda m, pattern=pattern: pattern.match(m["name"]))
        elif res["kind"] == "Certificate" and res["group"] == "cert-manager.io" and secrets:
            name = spec(res, meta).get("secretName")
            cert = meta["name"]
            if name:
                add(secrets, meta["namespace"], lambda m, name=name, cert=cert: m["name"] == name and (
                    m.get("annotations") or {}).get("cert-manager.io/certificate-name") == cert)
    return found


def delete(api, p, module):
    selector = "%s/env=%s,%s/component=%s" % (p["label_prefix"], p["env"], p["label_prefix"], p["component"])
    resources = _discover(api, set(p["skip_resources"]))
    objects = _all_objects(api, resources, selector)
    objects.update(_created_by(api, resources, objects, p))
    restored = _restore(api, resources, p, module)
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
            return {"changed": bool(deleted or restored), "deleted": deleted, "restored": restored,
                    "left": left, "phase": phase}
    return {"changed": bool(deleted or restored), "deleted": deleted, "restored": restored, "left": []}


def main():
    module = AnsibleModule(
        argument_spec={
            "action": {"type": "str", "required": True, "choices": ["snapshot", "begin", "label", "delete"]},
            "state_file": {"type": "path"},
            "env": {"type": "str"},
            "layer": {"type": "str", "default": "unknown"},
            "component": {"type": "str"},
            "declared": {"type": "dict", "default": {}},
            "skip_resources": {"type": "list", "elements": "str", "default": []},
            "label_prefix": {"type": "str", "default": "ai-solutions.intel.com"},
            "restore_resources": {"type": "list", "elements": "str", "default": ["namespaces", "nodes"]},
            "timeout": {"type": "int", "default": 600},
        },
        required_if=[("action", "snapshot", ["state_file"]), ("action", "begin", ["state_file", "env", "component"]),
                     ("action", "label", ["state_file", "env", "component"]),
                     ("action", "delete", ["env", "component"])],
        supports_check_mode=True,
    )
    if client is None:
        module.fail_json(msg="the kubernetes Python client is required")
    p = module.params
    if p["action"] == "begin":
        module.exit_json(**begin(p, module))
    kubeconfig = os.environ.get("K8S_AUTH_KUBECONFIG") or os.environ.get("KUBECONFIG") or "~/.kube/config"
    if p["action"] != "delete" and not os.path.exists(os.path.expanduser(kubeconfig)):
        # Kubespray has not built the cluster yet: the next label call takes the baseline.
        _write(p["state_file"], None)
        module.exit_json(changed=False, reachable=False, labelled=[], recorded=[])
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
            module.exit_json(**snapshot(api, p, module))
        module.exit_json(**(label(api, p, module) if p["action"] == "label" else delete(api, p, module)))
    except ApiException as e:
        try:
            reason = json.loads(e.body).get("message", e.reason)
        except (TypeError, ValueError):
            reason = e.reason
        module.fail_json(msg="%s failed: %s %s" % (p["action"], e.status, reason))


if __name__ == "__main__":
    main()
