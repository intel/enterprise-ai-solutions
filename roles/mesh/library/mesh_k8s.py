# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible module — mutual TLS for the namespaces an env put in a mesh it does not run.

Where the installer runs the mesh (the istio component), one PeerAuthentication in
the root namespace makes the whole mesh STRICT, and Prometheus is in the mesh too.
Where it joins someone else's mesh (OpenShift Service Mesh 3), a mesh-wide policy
would change every namespace of that mesh, and the cluster's Prometheus is not in
it. So, with `mtls`, each namespace the env enrolled gets:

  - PeerAuthentication `default`, mode STRICT, unless it already has a namespace-wide
    one (no selector)
  - with `monitors`, the ports each ServiceMonitor and PodMonitor in it scrapes, open
    to plaintext (port-level PERMISSIVE) on the pods they select. Istio applies one
    workload PeerAuthentication per pod (the oldest), so when one already selects
    those pods the ports are added to it; otherwise allow-scrape-<monitor> is created
    (with a suffix per Service when one ServiceMonitor selects several).

A namespace is the env's when it carries `mesh_labels` (istio.io/dataplane-mode=ambient,
or the sidecar injection label) and either the ownership role labelled it
(<prefix>/env=<env>), or it records that this env added the mesh label to it
(annotation <prefix>/labels-before.<env>.<component>).

In sidecar mode only pods created after the label get a proxy. With `enroll`, the
Running pods in those namespaces that have no istio-proxy (container or native
sidecar init container) and are not opted out (sidecar.istio.io/inject: "false")
are traced to their Deployment, StatefulSet or DaemonSet. One the ownership role
labelled for this env is restarted (kubectl.kubernetes.io/restartedAt); any other
owner (an operator's, the admin's) is only reported. On a fresh install there is
none: roles label the namespace before they create pods.

Policies it creates carry the ownership labels of the component that runs, so that
component's teardown deletes them. A port it cannot resolve yet (no pod to read a
named port from) is reported, and picked up after a later component.
"""

import datetime
import hashlib
import json
import os
import re

from ansible.module_utils.basic import AnsibleModule

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
except ImportError:  # reported by main()
    client = None

    class ApiException(Exception):
        def __init__(self, status=None):
            super().__init__(status)
            self.status = status

DOCUMENTATION = r"""
module: mesh_k8s
short_description: Per-namespace STRICT mutual TLS, and plaintext for the ports Prometheus scrapes
options:
  env: {type: str, required: true, description: Env name, the <prefix>/env label}
  layer: {type: str, default: unknown, description: Layer, the <prefix>/layer label of created policies}
  component: {type: str, required: true, description: Component, the <prefix>/component label of created policies}
  label_prefix: {type: str, default: ai-solutions.intel.com}
  mode: {type: str, default: STRICT, description: mTLS mode of the namespace policy}
  monitors: {type: bool, default: true, description: Open the ports ServiceMonitors and PodMonitors scrape}
  mesh_labels: {type: dict, required: true, description: The labels that put a namespace in the mesh}
  mtls: {type: bool, default: true, description: Add the per-namespace PeerAuthentications}
  enroll: {type: bool, default: false, description: Restart our workloads whose pods have no sidecar yet}
"""

PROXY = "istio-proxy"
RESTARTED = "kubectl.kubernetes.io/restartedAt"
PA = ("security.istio.io", "v1", "peerauthentications")
MONITORS = (("ServiceMonitor", "servicemonitors"), ("PodMonitor", "podmonitors"))


def _selector(sel):
    """A LabelSelector as a labelSelector query string."""
    parts = ["%s=%s" % kv for kv in sorted((sel.get("matchLabels") or {}).items())]
    for e in sel.get("matchExpressions") or []:
        op, values = e.get("operator"), ",".join(e.get("values") or [])
        parts.append({"In": "%s in (%s)" % (e["key"], values), "NotIn": "%s notin (%s)" % (e["key"], values),
                      "Exists": e["key"], "DoesNotExist": "!" + e["key"]}[op])
    return ",".join(parts)


def _ours(ns, p):
    meta = ns.metadata
    labels, annotations = meta.labels or {}, meta.annotations or {}
    if any(labels.get(k) != v for k, v in p["mesh_labels"].items()):
        return False
    if labels.get(p["label_prefix"] + "/env") == p["env"]:
        return True
    before = "%s/labels-before.%s." % (p["label_prefix"], re.sub(r"[^A-Za-z0-9_.-]", "-", p["env"]))
    for key, value in annotations.items():
        if key.startswith(before):
            try:
                if set(p["mesh_labels"]) & set(json.loads(value)):
                    return True
            except ValueError:
                pass
    return False


def _list(custom, ns, group, version, plural):
    try:
        return custom.list_namespaced_custom_object(group, version, ns, plural)["items"]
    except ApiException as e:
        if e.status == 404:  # the CRD is not installed
            return []
        raise


def _pod_port(pods, name):
    for pod in pods:
        for c in pod.spec.containers or []:
            for port in c.ports or []:
                if port.name == name:
                    return port.container_port
    return None


def _port(value, pods):
    """A port number, or a port name resolved on the pods; None when no pod has it."""
    if value is None:
        return None
    if isinstance(value, int) or str(value).isdigit():
        return int(value)
    return _pod_port(pods, value)


def _targets(core, custom, ns):
    """(monitor, pod selector, ports, pods, unresolved) per workload a monitor in ns scrapes."""
    found = []
    for kind, plural in MONITORS:
        for mon in _list(custom, ns, "monitoring.coreos.com", "v1", plural):
            name, spec = "%s/%s" % (kind, mon["metadata"]["name"]), mon.get("spec") or {}
            names = (spec.get("namespaceSelector") or {}).get("matchNames")
            if names and ns not in names:
                continue  # scrapes another namespace, which gets its own policy there
            sel = _selector(spec.get("selector") or {})
            if kind == "ServiceMonitor":
                for svc in core.list_namespaced_service(ns, label_selector=sel).items:
                    if not svc.spec.selector:
                        continue
                    pods = core.list_namespaced_pod(ns, label_selector=_selector({"matchLabels": svc.spec.selector})).items
                    ports, missing = set(), []
                    for ep in spec.get("endpoints") or []:
                        want = ep.get("targetPort")
                        if want is None:
                            sp = [s for s in svc.spec.ports or [] if s.name == ep.get("port")]
                            want = (sp[0].target_port or sp[0].port) if sp else None
                        number = _port(want, pods)
                        if number:
                            ports.add(number)
                        else:
                            missing.append(str(want))
                    found.append((name, dict(svc.spec.selector), ports, pods, missing))
            else:
                match = (spec.get("selector") or {}).get("matchLabels")
                if not match:
                    continue  # a PeerAuthentication selects by matchLabels only
                pods = core.list_namespaced_pod(ns, label_selector=sel).items
                ports, missing = set(), []
                for ep in spec.get("podMetricsEndpoints") or []:
                    want = ep.get("portNumber") or ep.get("port") or ep.get("targetPort")
                    number = _port(want, pods)
                    if number:
                        ports.add(number)
                    else:
                        missing.append(str(want))
                found.append((name, dict(match), ports, pods, missing))
    return found


def _selects(pa, labels):
    match = ((pa.get("spec") or {}).get("selector") or {}).get("matchLabels")
    return bool(match) and all(labels.get(k) == v for k, v in match.items())


def _name(prefix, monitor):
    return re.sub(r"[^a-z0-9-]", "-", ("%s-%s" % (prefix, monitor.split("/", 1)[1])).lower())[:63].strip("-")


def _free_name(prefix, monitor, match, pas):
    """_name, or, when a policy has it (one monitor, several Services), a suffix from the selector."""
    name = _name(prefix, monitor)
    if not any(pa["metadata"]["name"] == name for pa in pas):
        return name
    digest = hashlib.sha256(json.dumps(match, sort_keys=True).encode()).hexdigest()[:8]
    return "%s-%s" % (name[:54].rstrip("-"), digest)


def _has_proxy(pod):
    return any(c.name == PROXY for c in (pod.spec.containers or []) + (pod.spec.init_containers or []))


def _opted_out(pod):
    meta = pod.metadata
    return "false" in ((meta.annotations or {}).get("sidecar.istio.io/inject"),
                       (meta.labels or {}).get("sidecar.istio.io/inject"))


def _controller(refs):
    return next((r for r in refs or [] if r.controller), None)


def _workload(apps, ns, pod):
    """(kind, object) of the Deployment/StatefulSet/DaemonSet that runs pod, else (kind of its owner, None)."""
    ref = _controller(pod.metadata.owner_references)
    if ref is None:
        return "Pod", None
    if ref.kind == "ReplicaSet":
        rs = apps.read_namespaced_replica_set(ref.name, ns)
        ref = _controller(rs.metadata.owner_references)
        if ref is None or ref.kind != "Deployment":
            return "ReplicaSet", None
        return "Deployment", apps.read_namespaced_deployment(ref.name, ns)
    if ref.kind == "StatefulSet":
        return ref.kind, apps.read_namespaced_stateful_set(ref.name, ns)
    if ref.kind == "DaemonSet":
        return ref.kind, apps.read_namespaced_daemon_set(ref.name, ns)
    return ref.kind, None


def enroll(core, apps, p, module, namespaces, now):
    """(restarted, reported): our workloads restarted, other owners' pods left as they are."""
    restarted, reported, seen = [], [], set()
    for ns in namespaces:
        for pod in core.list_namespaced_pod(ns, field_selector="status.phase=Running").items:
            if _has_proxy(pod) or _opted_out(pod):
                continue
            kind, obj = _workload(apps, ns, pod)
            if obj is None:
                reported.append("%s/%s (owned by a %s)" % (ns, pod.metadata.name, kind))
                continue
            name = "%s/%s/%s" % (ns, kind, obj.metadata.name)
            if name in seen:
                continue
            seen.add(name)
            labels = obj.metadata.labels or {}
            # One an operator runs (owner reference) is the operator's to roll, even when labelled.
            if labels.get(p["label_prefix"] + "/env") != p["env"] or obj.metadata.owner_references:
                reported.append("%s (not ours, pod %s)" % (name, pod.metadata.name))
                continue
            if not module.check_mode:
                patch = {"spec": {"template": {"metadata": {"annotations": {RESTARTED: now}}}}}
                patcher = {"Deployment": "deployment", "StatefulSet": "stateful_set", "DaemonSet": "daemon_set"}[kind]
                getattr(apps, "patch_namespaced_" + patcher)(obj.metadata.name, ns, patch)
            restarted.append(name)
    return restarted, reported


def run(core, custom, p, module, apps=None, now=None):
    owner = {p["label_prefix"] + "/env": p["env"], p["label_prefix"] + "/layer": p["layer"],
             p["label_prefix"] + "/component": p["component"]}
    changes, unresolved, namespaces = [], [], []

    def create(ns, name, spec):
        body = {"apiVersion": "/".join(PA[:2]), "kind": "PeerAuthentication",
                "metadata": {"name": name, "namespace": ns, "labels": owner}, "spec": spec}
        if not module.check_mode:
            body = custom.create_namespaced_custom_object(PA[0], PA[1], ns, PA[2], body)
        return body

    selector = ",".join("%s=%s" % kv for kv in sorted(p["mesh_labels"].items()))
    for ns in core.list_namespace(label_selector=selector).items:
        if not _ours(ns, p):
            continue
        name = ns.metadata.name
        namespaces.append(name)
        if not p["mtls"]:
            continue
        pas = _list(custom, name, *PA)
        if not any(not (pa.get("spec") or {}).get("selector") for pa in pas):
            # `default` is the usual name; one that selects workloads may hold it already.
            pa_name = "namespace-mtls" if any(pa["metadata"]["name"] == "default" for pa in pas) else "default"
            pas.append(create(name, pa_name, {"mtls": {"mode": p["mode"]}}))
            changes.append("%s/%s: %s" % (name, pa_name, p["mode"]))
        if not p["monitors"]:
            continue
        for monitor, match, ports, pods, missing in _targets(core, custom, name):
            unresolved += ["%s/%s port %s" % (name, monitor, m) for m in missing]
            if not ports:
                continue
            labels = [pod.metadata.labels or {} for pod in pods] or [match]
            hits = sorted((pa for pa in pas if any(_selects(pa, lab) for lab in labels)),
                          key=lambda pa: pa["metadata"].get("creationTimestamp") or "")
            if not hits:
                pa_name = _free_name("allow-scrape", monitor, match, pas)
                pas.append(create(name, pa_name, {
                    "selector": {"matchLabels": match}, "mtls": {"mode": "STRICT"},
                    "portLevelMtls": {str(port): {"mode": "PERMISSIVE"} for port in sorted(ports)}}))
                changes.append("%s/%s: %s plaintext (%s)" % (name, pa_name, ", ".join(map(str, sorted(ports))), monitor))
                continue
            pa = hits[0]  # the one Istio applies
            spec = pa.get("spec") or {}
            if (spec.get("mtls") or {}).get("mode") in ("PERMISSIVE", "DISABLE") and not spec.get("portLevelMtls"):
                continue
            have = spec.get("portLevelMtls") or {}
            add = {str(port): {"mode": "PERMISSIVE"} for port in sorted(ports) if str(port) not in have}
            if not add:
                continue
            if not module.check_mode:
                custom.patch_namespaced_custom_object(PA[0], PA[1], name, PA[2], pa["metadata"]["name"],
                                                      {"spec": {"portLevelMtls": add}})
            spec.setdefault("portLevelMtls", {}).update(add)
            pa["spec"] = spec
            changes.append("%s/%s: %s plaintext (%s)" % (name, pa["metadata"]["name"], ", ".join(sorted(add)), monitor))
    restarted, reported = enroll(core, apps, p, module, namespaces, now) if p["enroll"] else ([], [])
    return {"changed": bool(changes or restarted), "namespaces": namespaces, "policies": changes,
            "unresolved": unresolved, "restarted": restarted, "not_enrolled": reported}


def main():
    module = AnsibleModule(
        argument_spec={
            "env": {"type": "str", "required": True},
            "layer": {"type": "str", "default": "unknown"},
            "component": {"type": "str", "required": True},
            "label_prefix": {"type": "str", "default": "ai-solutions.intel.com"},
            "mode": {"type": "str", "default": "STRICT", "choices": ["STRICT", "PERMISSIVE"]},
            "monitors": {"type": "bool", "default": True},
            "mesh_labels": {"type": "dict", "required": True},
            "mtls": {"type": "bool", "default": True},
            "enroll": {"type": "bool", "default": False},
        },
        supports_check_mode=True,
    )
    if client is None:
        module.fail_json(msg="the kubernetes Python client is required")
    kubeconfig = os.environ.get("K8S_AUTH_KUBECONFIG") or os.environ.get("KUBECONFIG") or "~/.kube/config"
    try:
        cfg = client.Configuration()
        config.load_kube_config(config_file=kubeconfig, client_configuration=cfg)
        # The client reads http(s)_proxy from the environment but not no_proxy.
        cfg.no_proxy = os.environ.get("K8S_AUTH_NO_PROXY") or os.environ.get("no_proxy") or os.environ.get("NO_PROXY")
        api = client.ApiClient(cfg)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        module.exit_json(**run(client.CoreV1Api(api), client.CustomObjectsApi(api), module.params, module,
                               apps=client.AppsV1Api(api), now=now))
    except ApiException as e:
        try:
            reason = json.loads(e.body).get("message", e.reason)
        except (TypeError, ValueError):
            reason = e.reason
        module.fail_json(msg="mesh policies failed: %s %s" % (e.status, reason))


if __name__ == "__main__":
    main()
