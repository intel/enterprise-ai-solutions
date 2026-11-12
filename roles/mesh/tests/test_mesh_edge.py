# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for mesh_edge, against in-memory fakes of the Kubernetes APIs.

    python -m pytest roles/mesh/tests
"""

import copy
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "library"))
import mesh_edge  # noqa: E402  pylint: disable=wrong-import-position

PREFIX = "ai-solutions.intel.com"
GW_NS = "envoy-gateway-system"
OWNER = {PREFIX + "/env": "ocp", PREFIX + "/mesh-edge": "eg-gateway"}


def route(ns, name, backends, hostnames=None, section="https", gateway="eg-gateway", paths=None):
    rule = {"backendRefs": backends}
    if paths:
        rule["matches"] = [{"path": p} for p in paths]
    spec = {"parentRefs": [{"name": gateway, "namespace": GW_NS, "sectionName": section}], "rules": [rule]}
    if hostnames:
        spec["hostnames"] = hostnames
    return {"metadata": {"namespace": ns, "name": name}, "spec": spec}


class Core:
    def __init__(self, namespaces=(GW_NS,), terminating=None):
        self.namespaces, self.terminating = namespaces, terminating

    def read_namespace(self, name):
        if name not in self.namespaces:
            raise mesh_edge.ApiException(status=404)
        return NS(metadata=NS(deletion_timestamp=self.terminating))


class Custom:
    def __init__(self, routes=(), listeners=None, objects=None):
        self.routes = list(routes)
        self.listeners = listeners or [{"name": "https"}, {"name": "https-apex"}]
        self.objects = objects or {}
        self.created, self.replaced, self.deleted = [], [], []

    def get_namespaced_custom_object(self, group, version, namespace, plural, name):
        return {"spec": {"listeners": self.listeners}}

    def list_cluster_custom_object(self, group, version, plural):
        return {"items": copy.deepcopy(self.routes)}

    def list_namespaced_custom_object(self, group, version, namespace, plural):
        return {"items": [copy.deepcopy(o) for o in self.objects.get(plural, {}).values()]}

    def create_namespaced_custom_object(self, group, version, namespace, plural, body):
        self.objects.setdefault(plural, {})[body["metadata"]["name"]] = body
        self.created.append((body["kind"], body["metadata"]["name"]))

    def replace_namespaced_custom_object(self, group, version, namespace, plural, name, body):
        self.objects[plural][name] = body
        self.replaced.append((body["kind"], name))

    def delete_namespaced_custom_object(self, group, version, namespace, plural, name):
        del self.objects[plural][name]
        self.deleted.append((plural, name))


class Module:
    check_mode = False


def params(**kw):
    return dict({"env": "ocp", "gateway": "eg-gateway", "gateway_namespace": GW_NS, "label_prefix": PREFIX,
                 "cluster_domain": "cluster.local"}, **kw)


def svc(name, port, **kw):
    return dict({"name": name, "port": port}, **kw)


APEX = "erag.apps.example"
ROUTES = [
    route("keycloak", "keycloak", [svc("keycloak-service", 8080)], ["keycloak." + APEX]),
    route("rag-ui", "ui-chart-route", [svc("ui-chart", 4173)], [APEX], "https-apex"),
    route("mcp-gateway", "mcp-gateway-route", [svc("mcp-gateway-svc", 8000)], [APEX], "https-apex",
          paths=[{"type": "PathPrefix", "value": "/api/v1/mcp"}]),
    route("llm-inference", "model", [{"group": "inference.networking.k8s.io", "kind": "InferencePool",
                                      "name": "pool", "port": 8000}], [APEX], "https-apex"),
    route("envoy-gateway-system", "http-to-https-redirect", [], section="http"),
]


def test_each_hostname_gets_an_entry_and_a_route_per_port():
    custom = Custom(ROUTES)
    out = mesh_edge.run(Core(), custom, params(), Module())
    assert out["hosts"] == [APEX, "keycloak." + APEX]
    se = custom.objects["serviceentries"]["edge-erag-apps-example"]
    assert se["metadata"]["labels"] == OWNER and se["metadata"]["namespace"] == GW_NS
    assert se["spec"] == {"exportTo": ["."], "hosts": [APEX], "location": "MESH_INTERNAL", "resolution": "NONE",
                          "ports": [{"number": 4173, "name": "http-4173", "protocol": "HTTP"},
                                    {"number": 8000, "name": "http-8000", "protocol": "HTTP"}]}
    vs = custom.objects["virtualservices"]["edge-erag-apps-example"]["spec"]
    assert vs["exportTo"] == ["."] and vs["hosts"] == [APEX]
    assert vs["http"] == [
        {"match": [{"port": 4173}], "route": [{"destination": {"host": "ui-chart.rag-ui.svc.cluster.local",
                                                               "port": {"number": 4173}}}]},
        {"match": [{"port": 8000}], "route": [{"destination": {"host": "mcp-gateway-svc.mcp-gateway.svc.cluster.local",
                                                               "port": {"number": 8000}}}]}]
    kc = custom.objects["virtualservices"]["edge-keycloak-erag-apps-example"]["spec"]["http"]
    assert kc == [{"match": [{"port": 8080}], "route": [{"destination": {
        "host": "keycloak-service.keycloak.svc.cluster.local", "port": {"number": 8080}}}]}]


def test_one_port_several_services_are_told_apart_by_path():
    routes = [
        route("a", "root", [svc("ui", 8000)], ["h.example"]),
        route("b", "api", [svc("api", 8000)], ["h.example"], paths=[{"type": "PathPrefix", "value": "/api"}]),
        route("c", "v1", [svc("v1", 8000)], ["h.example"], paths=[{"type": "PathPrefix", "value": "/api/v1"}]),
        route("d", "health", [svc("health", 8000)], ["h.example"], paths=[{"type": "Exact", "value": "/healthz"}]),
        route("e", "re", [svc("re", 8000)], ["h.example"], paths=[{"type": "RegularExpression", "value": "/x.*"}]),
    ]
    custom = Custom(routes)
    mesh_edge.run(Core(), custom, params(), Module())
    http = custom.objects["virtualservices"]["edge-h-example"]["spec"]["http"]
    assert [(h["match"][0].get("uri"), h["route"][0]["destination"]["host"].split(".")[0]) for h in http] == [
        ({"exact": "/healthz"}, "health"), ({"prefix": "/api/v1"}, "v1"), ({"prefix": "/api"}, "api"),
        ({"regex": "/x.*"}, "re"), (None, "ui")]
    assert all(h["match"][0]["port"] == 8000 for h in http)


def test_split_backends_keep_their_weights():
    custom = Custom([route("a", "r", [svc("blue", 80, weight=90), svc("green", 80, weight=10)], ["h.example"])])
    mesh_edge.run(Core(), custom, params(), Module())
    assert custom.objects["virtualservices"]["edge-h-example"]["spec"]["http"][0]["route"] == [
        {"destination": {"host": "blue.a.svc.cluster.local", "port": {"number": 80}}, "weight": 90},
        {"destination": {"host": "green.a.svc.cluster.local", "port": {"number": 80}}, "weight": 10}]


def test_listener_hostname_and_other_gateways():
    routes = [route("a", "r", [svc("s", 80)], section="named"),
              route("a", "other", [svc("t", 81)], ["other.example"], gateway="data-science-gateway"),
              route("a", "wild", [svc("w", 82)], ["*.example"])]
    custom = Custom(routes, listeners=[{"name": "named", "hostname": "named.example"}])
    out = mesh_edge.run(Core(), custom, params(), Module())
    assert out["hosts"] == ["named.example"]


def test_reconcile_updates_and_deletes_only_its_own():
    stale = {"metadata": {"name": "edge-gone-example", "labels": OWNER}, "spec": {"hosts": ["gone.example"]}}
    foreign = {"metadata": {"name": "edge-someone", "labels": {}}, "spec": {}}
    changed = {"metadata": {"name": "edge-keycloak-erag-apps-example", "labels": OWNER, "resourceVersion": "7"},
               "spec": {"hosts": ["keycloak." + APEX]}}
    objects = {"serviceentries": {"edge-gone-example": stale, "edge-someone": foreign},
               "virtualservices": {"edge-keycloak-erag-apps-example": changed}}
    custom = Custom(ROUTES[:1], objects=objects)
    out = mesh_edge.run(Core(), custom, params(), Module())
    assert custom.deleted == [("serviceentries", "edge-gone-example")]
    assert "edge-someone" in custom.objects["serviceentries"]
    assert custom.replaced == [("VirtualService", "edge-keycloak-erag-apps-example")]
    assert custom.objects["virtualservices"]["edge-keycloak-erag-apps-example"]["metadata"]["resourceVersion"] == "7"
    assert custom.created == [("ServiceEntry", "edge-keycloak-erag-apps-example")]
    again = mesh_edge.run(Core(), custom, params(), Module())
    assert out["changed"] and not again["changed"]


def test_one_of_the_same_name_that_is_not_ours_is_left_and_reported():
    mine = {"metadata": {"name": "edge-keycloak-erag-apps-example", "labels": {}}, "spec": {"hosts": ["x"]}}
    custom = Custom(ROUTES[:1], objects={"virtualservices": {mine["metadata"]["name"]: mine}})
    out = mesh_edge.run(Core(), custom, params(), Module())
    assert out["foreign"] == ["VirtualService edge-keycloak-erag-apps-example"]
    assert custom.objects["virtualservices"]["edge-keycloak-erag-apps-example"] is mine
    assert custom.created == [("ServiceEntry", "edge-keycloak-erag-apps-example")] and not custom.replaced


def test_no_routes_left_removes_everything():
    custom = Custom(ROUTES)
    mesh_edge.run(Core(), custom, params(), Module())
    custom.routes = []
    out = mesh_edge.run(Core(), custom, params(), Module())
    assert out["hosts"] == [] and not custom.objects["serviceentries"] and not custom.objects["virtualservices"]


def test_no_gateway_namespace_does_nothing():
    custom = Custom(ROUTES)
    assert mesh_edge.run(Core(namespaces=()), custom, params(), Module()) == {"changed": False, "hosts": [], "changes": [], "foreign": []}
    assert not custom.created
    assert not mesh_edge.run(Core(terminating="2026-11-12T00:00:00Z"), custom, params(), Module())["changed"]


def test_check_mode_changes_nothing():
    custom, module = Custom(ROUTES), Module()
    module.check_mode = True
    out = mesh_edge.run(Core(), custom, params(), module)
    assert out["changed"] and not custom.created and not custom.objects


def test_long_hostnames_fit_a_name():
    host = "a" * 40 + "." + "b" * 40 + ".example"
    name = mesh_edge._name(host)  # pylint: disable=protected-access
    assert len(name) <= 63 and name.startswith("edge-a") and name != mesh_edge._name(host + "x")  # pylint: disable=protected-access
