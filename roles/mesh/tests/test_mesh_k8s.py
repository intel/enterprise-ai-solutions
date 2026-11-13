# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for mesh_k8s, against in-memory fakes of the two Kubernetes APIs.

    python -m pytest roles/mesh/tests
"""

import copy
import datetime
import json
import os
import sys
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "library"))
import mesh_k8s  # noqa: E402  pylint: disable=wrong-import-position

PREFIX = "ai-solutions.intel.com"
AMBIENT = {"istio.io/dataplane-mode": "ambient"}
SIDECAR = {"istio-injection": "enabled"}


def _matches(labels, selector):
    for part in filter(None, (selector or "").split(",")):
        key, value = part.split("=")
        if labels.get(key) != value:
            return False
    return True


def ns(name, labels=None, annotations=None):
    return NS(metadata=NS(name=name, labels=dict(labels or {}), annotations=dict(annotations or {})))


def pod(name, labels, ports):
    return NS(metadata=NS(name=name, labels=labels),
              spec=NS(containers=[NS(ports=[NS(name=n, container_port=p) for n, p in ports.items()])]))


def svc(name, labels, selector, ports):
    return NS(metadata=NS(name=name, labels=labels),
              spec=NS(selector=selector, ports=[NS(name=n, port=p, target_port=t) for n, p, t in ports]))


class Core:
    def __init__(self, namespaces, pods=None, services=None):
        self.namespaces, self.pods, self.services = namespaces, pods or {}, services or {}

    def list_namespace(self, label_selector=None):
        return NS(items=[n for n in self.namespaces if _matches(n.metadata.labels, label_selector)])

    def patch_namespace(self, name, body):
        ns = next(n for n in self.namespaces if n.metadata.name == name)
        for field in ("labels", "annotations"):
            for key, value in (body["metadata"].get(field) or {}).items():
                if value is None:
                    getattr(ns.metadata, field).pop(key, None)
                else:
                    getattr(ns.metadata, field)[key] = value
        self.patched = getattr(self, "patched", []) + [(name, body)]

    def list_namespaced_pod(self, namespace, label_selector=None, field_selector=None):
        return NS(items=[p for p in self.pods.get(namespace, []) if _matches(p.metadata.labels, label_selector)])

    def list_namespaced_service(self, namespace, label_selector=None):
        return NS(items=[s for s in self.services.get(namespace, []) if _matches(s.metadata.labels, label_selector)])


class Custom:
    def __init__(self, objects=None, crds=("peerauthentications", "servicemonitors", "podmonitors")):
        self.objects, self.crds, self.created, self.patched = objects or {}, crds, [], []

    def list_namespaced_custom_object(self, group, version, namespace, plural):
        if plural not in self.crds:
            raise mesh_k8s.ApiException(status=404)
        return {"items": copy.deepcopy(self.objects.get((namespace, plural), []))}

    def create_namespaced_custom_object(self, group, version, namespace, plural, body):
        body = dict(body, metadata=dict(body["metadata"], creationTimestamp="2026-10-22T12:00:00Z"))
        self.objects.setdefault((namespace, plural), []).append(body)
        self.created.append(body)
        return body

    def patch_namespaced_custom_object(self, group, version, namespace, plural, name, body):
        self.patched.append((namespace, name, body))

    def list_cluster_custom_object(self, group, version, plural, label_selector=None):
        if plural not in self.crds:
            raise mesh_k8s.ApiException(status=404)
        return {"items": [copy.deepcopy(o) for (_, kind), objs in self.objects.items() if kind == plural
                          for o in objs if _matches(o["metadata"].get("labels") or {}, label_selector)]}

    def delete_namespaced_custom_object(self, group, version, namespace, plural, name):
        self.objects[(namespace, plural)] = [o for o in self.objects.get((namespace, plural), [])
                                             if o["metadata"]["name"] != name]
        self.deleted = getattr(self, "deleted", []) + [(namespace, name)]


class Module:
    check_mode = False


def params(**kw):
    return dict({"env": "ocp", "layer": "platform", "component": "keycloak", "label_prefix": PREFIX,
                 "mode": "STRICT", "monitors": True, "mesh_labels": AMBIENT, "mtls": True, "enroll": False,
                 "before": None}, **kw)


def record(component="keycloak", created=True, env="ocp"):
    return {"%s/mesh.%s" % (PREFIX, env): json.dumps({"component": component, "created": created}, sort_keys=True)}


def pa(name, selector=None, mode="STRICT", ports=None, created="2026-10-01T00:00:00Z"):
    spec = {"mtls": {"mode": mode}}
    if selector:
        spec["selector"] = {"matchLabels": selector}
    if ports:
        spec["portLevelMtls"] = {str(p): {"mode": "PERMISSIVE"} for p in ports}
    return {"metadata": {"name": name, "creationTimestamp": created}, "spec": spec}


def test_only_the_envs_namespaces_get_a_policy():
    core = Core([
        ns("ours", AMBIENT, record()),
        ns("other-env", AMBIENT, record(env="prod")),
        ns("admins", AMBIENT),
        ns("not-meshed", {}, record()),
        ns("bad", AMBIENT, {PREFIX + "/mesh.ocp": "not json"}),
    ])
    custom = Custom()
    out = mesh_k8s.run(core, custom, params(), Module())
    assert out["namespaces"] == ["ours"]
    assert [(c["metadata"]["namespace"], c["metadata"]["name"], c["spec"]) for c in custom.created] == \
        [("ours", "default", {"mtls": {"mode": "STRICT"}})]
    assert custom.created[0]["metadata"]["labels"] == {
        PREFIX + "/env": "ocp", PREFIX + "/layer": "platform", PREFIX + "/component": "keycloak"}


def test_namespaces_that_joined_since_the_snapshot_are_recorded():
    core = Core([ns("new", AMBIENT), ns("labelled", AMBIENT), ns("admins", AMBIENT), ns("plain")])
    before = mesh_k8s.snapshot(Core([ns("labelled"), ns("admins", AMBIENT), ns("plain")]), params())
    assert before == {"labelled": False, "admins": True, "plain": False}
    out = mesh_k8s.run(core, Custom(), params(before=before), Module())
    assert out["namespaces"] == ["labelled", "new"]
    assert json.loads(core.namespaces[0].metadata.annotations[PREFIX + "/mesh.ocp"]) == \
        {"component": "keycloak", "created": True, "labels": AMBIENT}
    assert json.loads(core.namespaces[1].metadata.annotations[PREFIX + "/mesh.ocp"]) == \
        {"component": "keycloak", "created": False, "labels": AMBIENT}
    assert core.namespaces[2].metadata.annotations == {}

    # A later component finds them by the record, and records nothing of its own.
    core.patched = []
    out = mesh_k8s.run(core, Custom(), params(component="gateway", before=mesh_k8s.snapshot(core, params())), Module())
    assert out["namespaces"] == ["labelled", "new"] and core.patched == []


def test_without_a_snapshot_nothing_is_recorded():
    core = Core([ns("new", AMBIENT)])
    out = mesh_k8s.run(core, Custom(), params(), Module())
    assert out["namespaces"] == [] and getattr(core, "patched", []) == []


def test_teardown_removes_the_components_policies_and_records():
    owner = {PREFIX + "/env": "ocp", PREFIX + "/layer": "platform", PREFIX + "/component": "keycloak"}
    other = dict(owner, **{PREFIX + "/component": "gateway"})
    core = Core([ns("keycloak", AMBIENT, record()), ns("gw", AMBIENT, record("gateway"))])
    custom = Custom({
        ("keycloak", "peerauthentications"): [dict(pa("default"), metadata={"name": "default", "namespace": "keycloak",
                                                                            "labels": owner}),
                                              pa("admins-own")],
        ("gw", "peerauthentications"): [dict(pa("default"), metadata={"name": "default", "namespace": "gw",
                                                                      "labels": other})],
    })
    out = mesh_k8s.remove(core, custom, params(), Module())
    assert out == {"changed": True, "policies": ["keycloak/default"], "namespaces": ["keycloak"]}
    assert [o["metadata"]["name"] for o in custom.objects[("keycloak", "peerauthentications")]] == ["admins-own"]
    assert core.namespaces[0].metadata.annotations == {} and core.namespaces[0].metadata.labels == {}
    assert core.namespaces[1].metadata.annotations != {} and core.namespaces[1].metadata.labels == AMBIENT
    assert mesh_k8s.remove(core, Custom(crds=()), params(), Module())["changed"] is False


def test_an_existing_namespace_policy_is_left_alone():
    core = Core([ns("ours", AMBIENT, record())])
    custom = Custom({("ours", "peerauthentications"): [pa("admins-own", mode="PERMISSIVE")]})
    out = mesh_k8s.run(core, custom, params(), Module())
    assert custom.created == [] and out["changed"] is False


def test_default_taken_by_a_workload_policy():
    core = Core([ns("ours", AMBIENT, record())])
    custom = Custom({("ours", "peerauthentications"): [pa("default", {"app": "x"})]})
    mesh_k8s.run(core, custom, params(), Module())
    assert [c["metadata"]["name"] for c in custom.created] == ["namespace-mtls"]


def test_scraped_ports_get_their_own_policy():
    labels = {"app": "keycloak"}
    core = Core(
        [ns("keycloak", AMBIENT, record())],
        pods={"keycloak": [pod("keycloak-0", dict(labels, extra="1"), {"http": 8080, "management": 9000})]},
        services={"keycloak": [svc("keycloak-service", {"app": "keycloak"}, labels,
                                   [("http", 8080, 8080), ("management", 9000, "management")])]},
    )
    custom = Custom({("keycloak", "servicemonitors"): [{
        "metadata": {"name": "keycloak"},
        "spec": {"selector": {"matchLabels": {"app": "keycloak"}}, "endpoints": [{"port": "management"}]}}]})
    out = mesh_k8s.run(core, custom, params(), Module())
    scrape = [c for c in custom.created if c["metadata"]["name"] == "allow-scrape-keycloak"]
    assert scrape and scrape[0]["spec"] == {
        "selector": {"matchLabels": labels}, "mtls": {"mode": "STRICT"},
        "portLevelMtls": {"9000": {"mode": "PERMISSIVE"}}}
    assert out["unresolved"] == []


def test_one_monitor_over_several_services_gets_a_policy_each():
    """KServe: one scheduler ServiceMonitor, one Service (and pod selector) per model."""
    part = {"app.kubernetes.io/component": "scheduler"}
    models = ("bge-reranker", "llama3")
    core = Core(
        [ns("llm", AMBIENT, record())],
        pods={"llm": [pod(m + "-epp", dict(part, name=m), {"metrics": 9090}) for m in models]},
        services={"llm": [svc(m + "-epp-service", part, dict(part, name=m), [("metrics", 9090, "metrics")])
                          for m in models]},
    )
    custom = Custom({("llm", "servicemonitors"): [{
        "metadata": {"name": "scheduler"},
        "spec": {"selector": {"matchLabels": part}, "endpoints": [{"port": "metrics"}]}}]})
    mesh_k8s.run(core, custom, params(), Module())
    scrape = [c for c in custom.created if c["metadata"]["name"].startswith("allow-scrape-")]
    assert [c["spec"]["selector"]["matchLabels"]["name"] for c in scrape] == list(models)
    names = [c["metadata"]["name"] for c in scrape]
    assert names[0] == "allow-scrape-scheduler" and names[1].startswith("allow-scrape-scheduler-")
    assert len(set(names)) == 2 and all(len(n) <= 63 for n in names)

    # A second run finds both by selector and creates nothing.
    custom.created = []
    mesh_k8s.run(core, custom, params(), Module())
    assert custom.created == []


def test_ports_join_the_workload_policy_istio_applies():
    """Two policies selecting one pod: Istio uses the oldest, so the port goes there."""
    labels = {"app.kubernetes.io/part-of": "llminferenceservice", "role": "scheduler"}
    core = Core(
        [ns("llm", AMBIENT, record())],
        pods={"llm": [pod("sched", labels, {"grpc": 9002, "metrics": 9090})]},
        services={"llm": [svc("epp", {"svc": "epp"}, {"role": "scheduler"}, [("metrics", 9090, "metrics")])]},
    )
    custom = Custom({
        ("llm", "peerauthentications"): [
            pa("newer", {"role": "scheduler"}, created="2026-10-10T00:00:00Z"),
            pa("allow-inference-gateway", {"app.kubernetes.io/part-of": "llminferenceservice"}, ports=[8000, 9002]),
        ],
        ("llm", "servicemonitors"): [{"metadata": {"name": "sched"},
                                      "spec": {"selector": {"matchLabels": {"svc": "epp"}},
                                               "endpoints": [{"port": "metrics"}]}}],
    })
    mesh_k8s.run(core, custom, params(), Module())
    assert custom.patched == [("llm", "allow-inference-gateway",
                               {"spec": {"portLevelMtls": {"9090": {"mode": "PERMISSIVE"}}}})]
    assert [c["metadata"]["name"] for c in custom.created] == ["default"]


def test_port_already_open_is_not_patched_again():
    labels = {"app": "vllm"}
    core = Core([ns("llm", AMBIENT, record())],
                pods={"llm": [pod("vllm", labels, {"": 8000})]})
    custom = Custom({
        ("llm", "peerauthentications"): [pa("gw", labels, ports=[8000])],
        ("llm", "podmonitors"): [{"metadata": {"name": "vllm"},
                                  "spec": {"selector": {"matchLabels": labels},
                                           "podMetricsEndpoints": [{"targetPort": 8000}]}}],
    })
    out = mesh_k8s.run(core, custom, params(), Module())
    assert custom.patched == [] and out["policies"] == ["llm/default: STRICT"]


def test_permissive_workload_policy_needs_nothing():
    labels = {"gateway": "edge"}
    core = Core([ns("eg", AMBIENT, record())],
                pods={"eg": [pod("envoy", labels, {"metrics": 19001})]})
    custom = Custom({
        ("eg", "peerauthentications"): [pa("edge", labels, mode="PERMISSIVE")],
        ("eg", "podmonitors"): [{"metadata": {"name": "envoy"},
                                 "spec": {"selector": {"matchLabels": labels},
                                          "podMetricsEndpoints": [{"port": "metrics"}]}}],
    })
    mesh_k8s.run(core, custom, params(), Module())
    assert custom.patched == [] and [c["metadata"]["name"] for c in custom.created] == ["default"]


def test_named_port_without_pods_is_reported():
    core = Core([ns("ours", AMBIENT, record())])
    custom = Custom({("ours", "podmonitors"): [{"metadata": {"name": "later"},
                                                "spec": {"selector": {"matchLabels": {"app": "later"}},
                                                         "podMetricsEndpoints": [{"port": "metrics"}]}}]})
    out = mesh_k8s.run(core, custom, params(), Module())
    assert out["unresolved"] == ["ours/PodMonitor/later port metrics"]
    assert [c["metadata"]["name"] for c in custom.created] == ["default"]


def test_monitor_crds_missing_and_monitors_off():
    core = Core([ns("ours", AMBIENT, record())])
    out = mesh_k8s.run(core, Custom(crds=("peerauthentications",)), params(), Module())
    assert out["policies"] == ["ours/default: STRICT"]
    out = mesh_k8s.run(core, Custom(), params(monitors=False), Module())
    assert out["policies"] == ["ours/default: STRICT"]


def test_check_mode_changes_nothing():
    core = Core([ns("ours", AMBIENT, record())])
    custom = Custom()
    module = Module()
    module.check_mode = True
    out = mesh_k8s.run(core, custom, params(), module)
    assert out["changed"] is True and custom.created == []


def test_selector_string():
    assert mesh_k8s._selector({"matchLabels": {"b": "2", "a": "1"},
                               "matchExpressions": [{"key": "c", "operator": "In", "values": ["x", "y"]},
                                                    {"key": "d", "operator": "DoesNotExist"}]}) \
        == "a=1,b=2,c in (x,y),!d"


# ── sidecar mode: enroll ─────────────────────────────────────────────────────

def ref(kind, name):
    return NS(kind=kind, name=name, controller=True)


def running(name, owner, containers=("app",), init=(), annotations=None, created=None, deleted=None,
            host_network=None):
    return NS(metadata=NS(name=name, labels={}, annotations=annotations or {}, owner_references=[owner],
                          creation_timestamp=created, deletion_timestamp=deleted),
              spec=NS(containers=[NS(name=c, ports=[]) for c in containers],
                      init_containers=[NS(name=c) for c in init], host_network=host_network))


def workload(name, labels=None, owners=None, restarted=None):
    annotations = {mesh_k8s.RESTARTED: restarted} if restarted else None
    return NS(metadata=NS(name=name, labels=labels or {}, owner_references=owners),
              spec=NS(template=NS(metadata=NS(annotations=annotations))))


class Apps:
    def __init__(self, replicasets=None, deployments=None, statefulsets=None, daemonsets=None):
        self.objects = {"replica_set": replicasets or {}, "deployment": deployments or {},
                        "stateful_set": statefulsets or {}, "daemon_set": daemonsets or {}}
        self.patched = []

    def _read(self, kind, name):
        if name not in self.objects[kind]:
            raise mesh_k8s.ApiException(status=404)
        return self.objects[kind][name]

    def read_namespaced_replica_set(self, name, ns):
        return self._read("replica_set", name)

    def read_namespaced_deployment(self, name, ns):
        return self._read("deployment", name)

    def read_namespaced_stateful_set(self, name, ns):
        return self._read("stateful_set", name)

    def read_namespaced_daemon_set(self, name, ns):
        return self._read("daemon_set", name)

    def patch_namespaced_deployment(self, name, ns, body):
        self.patched.append(("Deployment", ns, name, body))

    def patch_namespaced_stateful_set(self, name, ns, body):
        self.patched.append(("StatefulSet", ns, name, body))

    def patch_namespaced_daemon_set(self, name, ns, body):
        self.patched.append(("DaemonSet", ns, name, body))


def test_sidecar_labels_select_the_namespaces():
    core = Core([ns("ours", SIDECAR, record()), ns("ambient", AMBIENT, record())])
    out = mesh_k8s.run(core, Custom(), params(mesh_labels=SIDECAR), Module(), apps=Apps())
    assert out["namespaces"] == ["ours"]


def test_enroll_restarts_ours_and_reports_the_rest():
    """In a namespace the component created: what no operator runs is restarted."""
    pods = [running("api-1", ref("ReplicaSet", "api-7f")),
            running("api-2", ref("ReplicaSet", "api-7f")),
            running("db-0", ref("StatefulSet", "db")),
            running("kc-0", ref("StatefulSet", "keycloak")),
            running("meshed-0", ref("StatefulSet", "db"), init=("istio-proxy",)),
            running("old-1", ref("ReplicaSet", "old-1"), containers=("app", "istio-proxy")),
            running("out-0", ref("StatefulSet", "db"), annotations={"sidecar.istio.io/inject": "false"}),
            running("job-x", ref("Job", "job"))]
    apps = Apps(replicasets={"api-7f": workload("api-7f", owners=[ref("Deployment", "api")])},
                deployments={"api": workload("api")},
                statefulsets={"db": workload("db"),
                              "keycloak": workload("keycloak", owners=[ref("Keycloak", "keycloak")])})
    core = Core([ns("ours", SIDECAR, record())], pods={"ours": pods})
    out = mesh_k8s.run(core, Custom(), params(mesh_labels=SIDECAR, mtls=False, enroll=True), Module(),
                       apps=apps, now="2026-11-02T00:00:00Z")
    assert out["restarted"] == ["ours/Deployment/api", "ours/StatefulSet/db"]
    assert [p[:3] for p in apps.patched] == [("Deployment", "ours", "api"), ("StatefulSet", "ours", "db")]
    assert apps.patched[0][3]["spec"]["template"]["metadata"]["annotations"] == {mesh_k8s.RESTARTED: "2026-11-02T00:00:00Z"}
    assert out["not_enrolled"] == ["ours/StatefulSet/keycloak (not ours, pod kc-0)", "ours/job-x (owned by a Job)"]
    assert out["policies"] == [] and out["changed"] is True


def test_enroll_leaves_a_namespace_it_did_not_create():
    apps = Apps(deployments={"rhbk-operator": workload("rhbk-operator")},
                replicasets={"rhbk-1": workload("rhbk-1", owners=[ref("Deployment", "rhbk-operator")])})
    core = Core([ns("keycloak", SIDECAR, record(created=False))],
                pods={"keycloak": [running("rhbk-1-x", ref("ReplicaSet", "rhbk-1"))]})
    out = mesh_k8s.run(core, Custom(), params(mesh_labels=SIDECAR, mtls=False, enroll=True), Module(),
                       apps=apps, now="x")
    assert out["restarted"] == [] and apps.patched == []
    assert out["not_enrolled"] == ["keycloak/Deployment/rhbk-operator (not ours, pod rhbk-1-x)"]


def test_enroll_skips_namespaces_an_earlier_component_joined():
    apps = Apps(statefulsets={"db": workload("db")})
    core = Core([ns("pg", SIDECAR, record(component="postgresql"))],
                pods={"pg": [running("db-0", ref("StatefulSet", "db"))]})
    out = mesh_k8s.run(core, Custom(), params(mesh_labels=SIDECAR, mtls=False, enroll=True), Module(),
                       apps=apps, now="x")
    assert out["namespaces"] == ["pg"]
    assert out["restarted"] == [] and out["not_enrolled"] == [] and apps.patched == []


def test_enroll_check_mode_patches_nothing():
    apps = Apps(statefulsets={"db": workload("db")})
    core = Core([ns("ours", SIDECAR, record())], pods={"ours": [running("db-0", ref("StatefulSet", "db"))]})
    module = Module()
    module.check_mode = True
    out = mesh_k8s.run(core, Custom(), params(mesh_labels=SIDECAR, mtls=False, enroll=True), module, apps=apps, now="x")
    assert out["restarted"] == ["ours/StatefulSet/db"] and apps.patched == []


def test_enroll_restarts_a_daemonset_and_reports_what_it_cannot_trace():
    pods = [running("agent-x", ref("DaemonSet", "agent")),
            running("bare-1", ref("ReplicaSet", "bare")),
            running("gone-1", ref("ReplicaSet", "gone")),
            running("orphan-1", ref("ReplicaSet", "orphan-rs"))]
    apps = Apps(daemonsets={"agent": workload("agent")},
                replicasets={"bare": workload("bare"),
                             "orphan-rs": workload("orphan-rs", owners=[ref("Deployment", "deleted")])})
    core = Core([ns("ours", SIDECAR, record())], pods={"ours": pods})
    out = mesh_k8s.run(core, Custom(), params(mesh_labels=SIDECAR, mtls=False, enroll=True), Module(),
                       apps=apps, now="x")
    assert out["restarted"] == ["ours/DaemonSet/agent"] and [p[0] for p in apps.patched] == ["DaemonSet"]
    assert out["not_enrolled"] == ["ours/bare-1 (owned by a ReplicaSet)", "ours/gone-1 (owned by a ReplicaSet)",
                                   "ours/orphan-1 (owned by a Deployment)"]


def test_enroll_skips_terminating_host_network_and_rolling_pods():
    started = datetime.datetime(2026, 11, 2, tzinfo=datetime.timezone.utc)
    pods = [running("term-0", ref("StatefulSet", "db"), deleted=started),
            running("host-0", ref("StatefulSet", "db"), host_network=True),
            running("old-1", ref("ReplicaSet", "web-1"), created=started)]
    apps = Apps(statefulsets={"db": workload("db")},
                replicasets={"web-1": workload("web-1", owners=[ref("Deployment", "web")])},
                deployments={"web": workload("web", restarted="2026-11-02T00:05:00Z")})
    core = Core([ns("ours", SIDECAR, record())], pods={"ours": pods})
    out = mesh_k8s.run(core, Custom(), params(mesh_labels=SIDECAR, mtls=False, enroll=True), Module(),
                       apps=apps, now="x")
    assert out["restarted"] == [] and out["not_enrolled"] == [] and apps.patched == []
    # A restart from before the pod started does not count.
    apps.objects["deployment"]["web"] = workload("web", restarted="2026-11-01T23:00:00+00:00")
    out = mesh_k8s.run(core, Custom(), params(mesh_labels=SIDECAR, mtls=False, enroll=True), Module(),
                       apps=apps, now="x")
    assert out["restarted"] == ["ours/Deployment/web"]


def test_a_podmonitor_with_match_expressions_is_reported():
    mon = {"metadata": {"name": "m"}, "spec": {
        "selector": {"matchLabels": {"app": "a"}, "matchExpressions": [{"key": "tier", "operator": "Exists"}]},
        "podMetricsEndpoints": [{"portNumber": 9090}]}}
    core = Core([ns("ours", AMBIENT, record())], pods={"ours": [pod("a-1", {"app": "a", "tier": "x"}, {})]})
    custom = Custom({("ours", "podmonitors"): [mon]})
    out = mesh_k8s.run(core, custom, params(), Module())
    assert out["unresolved"] == ["ours/PodMonitor/m selector with matchExpressions"]
    assert [o["metadata"]["name"] for o in custom.created] == ["default"]


def test_teardown_takes_off_the_labels_it_joined_with():
    """istio_enabled turned off since: the labels in the record go, not today's (none)."""
    joined = {"%s/mesh.ocp" % PREFIX: json.dumps({"component": "keycloak", "created": False, "labels": SIDECAR})}
    keycloak = ns("keycloak", dict(SIDECAR, team="x"), joined)
    core = Core([keycloak])
    out = mesh_k8s.remove(core, Custom(), params(mesh_labels={}, state="absent"), Module())
    assert out["namespaces"] == ["keycloak"]
    assert keycloak.metadata.labels == {"team": "x"} and keycloak.metadata.annotations == {}
