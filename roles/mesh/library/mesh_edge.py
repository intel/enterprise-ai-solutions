# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible module — the gateway's hostnames, for its sidecar.

With sidecars Envoy Gateway sends each request to the backend Service
(routingType: Service) with the client's Host header, like keycloak.<domain>. The
gateway pod's sidecar picks the outbound route by that Host, and it only knows the
mesh's hosts: an external one falls through to PassthroughCluster, in plaintext,
which a STRICT backend resets (503). A VirtualService alone does not add a host the
mesh does not know.

So each hostname the HTTPRoutes attached to the gateway serve gets, in the gateway's
namespace and visible there only (exportTo "."):

  - ServiceEntry edge-<host>: the hostname, MESH_INTERNAL, resolution NONE, with
    the routes' backend ports as HTTP
  - VirtualService edge-<host>: per port, to the backend Service. Several hostnames
    share the apex, each on its own port; where one hostname and port lead to
    several Services, the route's path matches pick the Service (Exact, then the
    longest prefix, then regular expressions).

Both carry <prefix>/env and <prefix>/mesh-edge=<gateway>. Every call reconciles
all of them: one for a hostname no route serves any more is deleted, so it runs
after each component's install and teardown. One of the same name without those
labels is someone else's: it is left alone and reported. state=absent deletes all
of ours (teardown with the mesh turned off since). Backends other than Services (an
InferencePool) are left to the gateway. Nothing happens when the gateway's namespace
does not exist or is being deleted.
"""

import hashlib
import json
import os
import re

from ansible.module_utils.basic import AnsibleModule

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    from kubernetes.config import ConfigException
    from urllib3.exceptions import HTTPError
except ImportError:  # reported by main()
    client = None
    ConfigException = HTTPError = Exception

    class ApiException(Exception):
        def __init__(self, status=None):
            super().__init__(status)
            self.status = status

GATEWAY = ("gateway.networking.k8s.io", "v1", "gateways")
HTTPROUTE = ("gateway.networking.k8s.io", "v1", "httproutes")
SE = ("networking.istio.io", "v1", "serviceentries")
VS = ("networking.istio.io", "v1", "virtualservices")
KINDS = {SE: "ServiceEntry", VS: "VirtualService"}


def _name(host, hashed=False):
    """edge-<host>; with a hash of the host when too long, or when hashed (two hosts
    that read the same, like a-b.example and a.b.example)."""
    name = "edge-" + re.sub(r"[^a-z0-9-]", "-", host.lower()).strip("-")
    if hashed or len(name) > 63:
        name = name[:54].rstrip("-") + "-" + hashlib.sha256(host.encode()).hexdigest()[:8]
    return name


def _attached(route, gateway, namespace):
    """The listeners (sectionName, None for all) the route attaches to on the gateway."""
    own = route["metadata"]["namespace"]
    return [ref.get("sectionName") for ref in (route.get("spec") or {}).get("parentRefs") or []
            if ref.get("kind", "Gateway") == "Gateway" and ref.get("name") == gateway
            and ref.get("namespace", own) == namespace]


def _match(path):
    kind, value = (path or {}).get("type", "PathPrefix"), (path or {}).get("value", "/")
    if kind == "Exact":
        return (0, 0, {"uri": {"exact": value}})
    if kind == "RegularExpression":
        return (2, 0, {"uri": {"regex": value}})
    if value == "/":
        return (3, 0, None)
    return (1, -len(value), {"uri": {"prefix": value}})


def hosts(routes, listeners, gateway, namespace, domain):
    """hostname -> port -> [(sort key, uri match or None, [(service FQDN, weight)])]"""
    out = {}
    for route in routes:
        sections = _attached(route, gateway, namespace)
        if not sections:
            continue
        spec, own = route.get("spec") or {}, route["metadata"]["namespace"]
        # Without a sectionName the route attaches to every listener.
        names = spec.get("hostnames") or sorted(
            {h for s, h in listeners.items() if h and (s in sections or None in sections)})
        for rule in spec.get("rules") or []:
            refs = [r for r in rule.get("backendRefs") or []
                    if r.get("group", "") == "" and r.get("kind", "Service") == "Service" and r.get("port")]
            if not refs:
                continue
            for port in sorted({r["port"] for r in refs}):
                dests = [("%s.%s.svc.%s" % (r["name"], r.get("namespace", own), domain), r.get("weight", 1))
                         for r in refs if r["port"] == port]
                paths = [m.get("path") for m in rule.get("matches") or []] or [None]
                for host in names:
                    if "*" in host:
                        continue
                    for path in paths:
                        out.setdefault(host, {}).setdefault(port, []).append(_match(path) + (dests,))
    return out


def desired(found, owner):
    objects, names = {}, {}
    for host in found:
        names.setdefault(_name(host), []).append(host)
    for host, ports in sorted(found.items()):
        name, http = _name(host, len(names[_name(host)]) > 1), []
        for port, entries in sorted(ports.items()):
            services = {d for *_, dests in entries for d, _ in dests}
            if len(services) == 1:
                entries = [(3, 0, None, entries[0][3])]
            seen = []
            for _, _, match, dests in sorted(entries, key=lambda e: e[:2]):
                if match in seen:
                    continue
                seen.append(match)
                matches = [dict(match or {}, port=port)]
                route = [{"destination": {"host": d, "port": {"number": port}}} for d, _ in dests]
                if len(route) > 1:
                    for r, (_, weight) in zip(route, dests):
                        r["weight"] = weight
                http.append({"match": matches, "route": route})
        meta = {"name": name, "labels": owner}
        objects[(SE, name)] = {"metadata": meta, "spec": {
            "exportTo": ["."], "hosts": [host], "location": "MESH_INTERNAL", "resolution": "NONE",
            "ports": [{"number": port, "name": "http-%d" % port, "protocol": "HTTP"} for port in sorted(ports)]}}
        objects[(VS, name)] = {"metadata": meta, "spec": {"exportTo": ["."], "hosts": [host], "http": http}}
    return objects


def run(core, custom, p, module):
    namespace, gateway = p["gateway_namespace"], p["gateway"]
    try:
        terminating = core.read_namespace(namespace).metadata.deletion_timestamp
    except ApiException as e:
        if e.status != 404:
            raise
        terminating = True
    if terminating:
        return {"changed": False, "hosts": [], "changes": [], "foreign": []}
    owner = {"%s/env" % p["label_prefix"]: p["env"], "%s/mesh-edge" % p["label_prefix"]: gateway}
    try:
        gw = custom.get_namespaced_custom_object(*GATEWAY[:2], namespace, GATEWAY[2], gateway)
        listeners = {l["name"]: l.get("hostname") for l in (gw.get("spec") or {}).get("listeners") or []}
        routes = custom.list_cluster_custom_object(*HTTPROUTE)["items"]
    except ApiException as e:
        if e.status != 404:
            raise
        listeners, routes = {}, []
    want = desired(hosts(routes, listeners, gateway, namespace, p["cluster_domain"]), owner) \
        if p.get("state", "present") == "present" else {}
    changes, foreign = [], []
    for res in (SE, VS):
        try:
            items = custom.list_namespaced_custom_object(res[0], res[1], namespace, res[2])["items"]
        except ApiException as e:
            if e.status != 404:  # no Istio CRDs: nothing of ours either
                raise
            items = []
        have = {o["metadata"]["name"]: o for o in items}
        for name, obj in have.items():
            if any((obj["metadata"].get("labels") or {}).get(k) != v for k, v in owner.items()):
                if (res, name) in want:
                    foreign.append("%s %s" % (KINDS[res], name))
                continue
            if (res, name) not in want:
                if not module.check_mode:
                    custom.delete_namespaced_custom_object(res[0], res[1], namespace, res[2], name)
                changes.append("deleted %s %s" % (KINDS[res], name))
        for (kind, name), obj in sorted(want.items()):
            if kind != res:
                continue
            body = {"apiVersion": "/".join(res[:2]), "kind": KINDS[res],
                    "metadata": dict(obj["metadata"], namespace=namespace), "spec": obj["spec"]}
            current = have.get(name)
            if "%s %s" % (KINDS[res], name) in foreign:
                continue
            if current is None:
                if not module.check_mode:
                    custom.create_namespaced_custom_object(res[0], res[1], namespace, res[2], body)
                changes.append("created %s %s" % (KINDS[res], name))
            elif current.get("spec") != obj["spec"]:
                body["metadata"]["resourceVersion"] = current["metadata"].get("resourceVersion")
                if not module.check_mode:
                    custom.replace_namespaced_custom_object(res[0], res[1], namespace, res[2], name, body)
                changes.append("updated %s %s" % (KINDS[res], name))
    return {"changed": bool(changes), "hosts": sorted(o["spec"]["hosts"][0] for (k, _), o in want.items() if k == SE),
            "changes": changes, "foreign": foreign}


def main():
    module = AnsibleModule(
        argument_spec={
            "env": {"type": "str", "required": True},
            "gateway": {"type": "str", "required": True},
            "gateway_namespace": {"type": "str", "required": True},
            "label_prefix": {"type": "str", "default": "ai-solutions.intel.com"},
            "cluster_domain": {"type": "str", "default": "cluster.local"},
            "state": {"type": "str", "default": "present", "choices": ["present", "absent"]},
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
        module.exit_json(**run(client.CoreV1Api(api), client.CustomObjectsApi(api), module.params, module))
    except ApiException as e:
        try:
            reason = json.loads(e.body).get("message", e.reason)
        except (TypeError, ValueError):
            reason = e.reason
        module.fail_json(msg="mesh edge failed: %s %s" % (e.status, reason))
    except (ConfigException, HTTPError) as e:  # no kubeconfig, API server unreachable
        module.fail_json(msg="mesh edge failed: %s" % e)


if __name__ == "__main__":
    main()
