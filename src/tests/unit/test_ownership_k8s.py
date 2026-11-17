# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
"""roles/ownership/library/ownership_k8s.py against an in-memory API server."""
import base64
import copy
import gzip
import importlib.util
import json
import pathlib

import pytest
import yaml

_MODULE = pathlib.Path(__file__).resolve().parents[3] / "roles" / "ownership" / "library" / "ownership_k8s.py"
_spec = importlib.util.spec_from_file_location("ownership_k8s", _MODULE)
own = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(own)

PREFIX = "ai-solutions.intel.com"
COMPONENT = f"{PREFIX}/component"


class ApiException(Exception):
    def __init__(self, status, reason=""):
        super().__init__(status)
        self.status, self.reason, self.body = status, reason, None


@pytest.fixture(autouse=True)
def _api_exception(monkeypatch):
    # The kubernetes client is not installed for the unit tests.
    monkeypatch.setattr(own, "ApiException", ApiException, raising=False)


# (group, version, resource, kind, namespaced)
RESOURCES = [
    ("", "v1", "namespaces", "Namespace", False),
    ("", "v1", "configmaps", "ConfigMap", True),
    ("", "v1", "secrets", "Secret", True),
    ("", "v1", "services", "Service", True),
    ("", "v1", "persistentvolumeclaims", "PersistentVolumeClaim", True),
    ("apps", "v1", "deployments", "Deployment", True),
    ("apps", "v1", "statefulsets", "StatefulSet", True),
    ("apiextensions.k8s.io", "v1", "customresourcedefinitions", "CustomResourceDefinition", False),
    ("cert-manager.io", "v1", "certificates", "Certificate", True),
    ("serving.knative.dev", "v1", "services", "Service", True),
]


class Resp:
    def __init__(self, data):
        self.data = json.dumps(data).encode()


class FakeApi:
    """GET (discovery, lists, objects), merge PATCH of metadata and DELETE."""

    def __init__(self):
        self.objects = []  # {"res": RESOURCES entry, "metadata": {...}, "spec": {...}, "data": {...}}
        self.uid = 0
        self.fail = {}  # list path -> status
        self.calls = []

    def add(self, kind, name, namespace=None, group=None, labels=None, annotations=None, owners=None, **extra):
        res = next(r for r in RESOURCES if r[3] == kind and (group is None or r[0] == group))
        self.uid += 1
        meta = {"name": name, "uid": "uid-%d-%s" % (self.uid, name), "labels": dict(labels or {}),
                "annotations": dict(annotations or {})}
        if res[4]:
            meta["namespace"] = namespace
        if owners:
            meta["ownerReferences"] = owners
        self.objects.append(dict(extra, res=res, metadata=meta))
        return meta["uid"]

    def get(self, uid):
        return next(o for o in self.objects if o["metadata"]["uid"] == uid)

    def labels(self, uid):
        return self.get(uid)["metadata"]["labels"]

    def exists(self, uid):
        return any(o["metadata"]["uid"] == uid for o in self.objects)

    @staticmethod
    def _base(res):
        return "/api/v1" if res[0] == "" else "/apis/%s/%s" % (res[0], res[1])

    def _match(self, path):
        """(resource, namespace or None, name or None) of an object or list path."""
        for res in RESOURCES:
            base = self._base(res)
            if not path.startswith(base + "/"):
                continue
            parts = path[len(base) + 1:].split("/")
            namespace = None
            if parts[0] == "namespaces" and len(parts) >= 3 and res[2] != "namespaces":
                namespace, parts = parts[1], parts[2:]
            if parts[0] != res[2] or len(parts) > 2:
                continue
            return res, namespace, parts[1] if len(parts) > 1 else None
        return None

    def call_api(self, path, method, query_params=None, header_params=None, body=None, **_):
        self.calls.append((method, path))
        if path == "/version":
            return Resp({"gitVersion": "v1.33.0"})
        if path == "/apis":
            groups = sorted({r[0] for r in RESOURCES if r[0]})
            return Resp({"groups": [{"name": g, "preferredVersion": {"groupVersion": g + "/v1"},
                                     "versions": [{"groupVersion": g + "/v1"}]} for g in groups]})
        for base in {self._base(r) for r in RESOURCES}:
            if path == base:
                return Resp({"resources": [{"name": r[2], "kind": r[3], "namespaced": r[4],
                                            "verbs": ["list", "patch", "delete", "get"]}
                                           for r in RESOURCES if self._base(r) == base]})
        if path in self.fail:
            raise ApiException(self.fail[path])
        res, namespace, name = self._match(path)
        found = [o for o in self.objects if o["res"] == res and (namespace is None
                 or o["metadata"].get("namespace") == namespace) and (name is None or o["metadata"]["name"] == name)]
        if name is None:  # a list
            selector = dict(query_params or []).get("labelSelector")
            want = dict(kv.split("=", 1) for kv in selector.split(",")) if selector else {}
            items = [{"metadata": copy.deepcopy(o["metadata"])} for o in found
                     if all(o["metadata"]["labels"].get(k) == v for k, v in want.items())]
            return Resp({"items": items, "metadata": {}})
        if not found:
            raise ApiException(404)
        obj = found[0]
        if method == "GET":
            return Resp({k: copy.deepcopy(v) for k, v in obj.items() if k != "res"})
        if method == "DELETE":
            self.objects.remove(obj)
            return Resp({})
        if method == "PATCH":
            for key in ("labels", "annotations"):
                for k, v in ((body.get("metadata") or {}).get(key) or {}).items():
                    if v is None:
                        obj["metadata"][key].pop(k, None)
                    else:
                        obj["metadata"][key][k] = v
            return Resp({})
        raise AssertionError(method)


class Module:
    check_mode = False


def params(tmp_path, component="app", **extra):
    return dict({"state_file": str(tmp_path / "ownership-uids.json"), "env": "local", "layer": "platform",
                 "component": component, "declared": {}, "skip_resources": [], "label_prefix": PREFIX,
                 "restore_resources": ["namespaces", "nodes"], "timeout": 1, "component_failed": False,
                 "teardown": False}, **extra)


def record(p, **r):
    with open(own._record_file(p["state_file"]), "a") as f:
        f.write(json.dumps(dict(r, component=p["component"])) + "\n")


def helm_secret(api, name, namespace, version, manifest="", hooks=(), crds=()):
    release = {"manifest": manifest, "hooks": [{"manifest": h} for h in hooks],
               "chart": {"files": [{"name": "crds/%d.yaml" % i, "data": base64.b64encode(c.encode()).decode()}
                                   for i, c in enumerate(crds)]}}
    blob = base64.b64encode(base64.b64encode(gzip.compress(json.dumps(release).encode()))).decode()
    return api.add("Secret", "sh.helm.release.v1.%s.v%d" % (name, version), namespace,
                   labels={"owner": "helm", "name": name, "version": str(version)}, data={"release": blob})


def doc(kind, name, namespace=None, api_version="v1"):
    meta = {"name": name}
    if namespace:
        meta["namespace"] = namespace
    return yaml.safe_dump({"apiVersion": api_version, "kind": kind, "metadata": meta})


def test_u1_labels_exactly_what_the_component_created(tmp_path):
    api, p = FakeApi(), params(tmp_path)
    old_cm = api.add("ConfigMap", "settings", "shared")
    foreign_secret = helm_secret(api, "theirs", "shared", 1)
    foreign_obj = api.add("ConfigMap", "theirs-cm", "shared",
                          annotations={"meta.helm.sh/release-name": "theirs", "meta.helm.sh/release-namespace": "shared"})
    own.snapshot(api, p, Module())

    ns = api.add("Namespace", "app")
    cm = api.add("ConfigMap", "app-cm", "app")
    secret = api.add("Secret", "app-secret", "app")
    deploy = api.add("Deployment", "web", "app")
    declared = api.add("Secret", "erag-credentials", "app")
    other = api.add("ConfigMap", "other-cm", "app")
    cron = api.add("Secret", "cron-made", "app")
    manifest = doc("ConfigMap", "chart-cm", "app")
    rel = helm_secret(api, "app", "app", 1, manifest=manifest, hooks=[doc("Secret", "hook-secret", "app")],
                      crds=[doc("CustomResourceDefinition", "widgets.example.com",
                                api_version="apiextensions.k8s.io/v1")])
    chart_cm = api.add("ConfigMap", "chart-cm", "app")
    hook = api.add("Secret", "hook-secret", "app")
    crd = api.add("CustomResourceDefinition", "widgets.example.com")
    # Helm upgrades a release that was there: not the component's.
    record(p, type="release", name="theirs", namespace="shared")
    record(p, type="object", uid=ns, kind="Namespace", name="app")
    record(p, type="object", uid=cm, kind="ConfigMap", name="app-cm", namespace="app")
    record(p, type="object", uid=old_cm, kind="ConfigMap", name="settings", namespace="shared", method="update")
    record(p, type="named", kind="Secret", api_version="v1", name="app-secret", namespace="app")
    record(p, type="cli", resource="deployment.apps", name="web", namespace="app")
    record(p, type="release", name="app", namespace="app")
    with open(own._record_file(p["state_file"]), "a") as f:
        f.write(json.dumps({"type": "object", "uid": other, "component": "other"}) + "\n")
    p["declared"] = {"app": [{"kind": "Secret", "name": "erag-credentials"}]}

    result = own.label(api, p, Module())

    mine = {ns, cm, secret, deploy, declared, rel, chart_cm, hook, crd}
    for uid in mine:
        assert api.labels(uid)[COMPONENT] == "app", api.get(uid)["metadata"]["name"]
    for uid in (old_cm, other, cron, foreign_secret, foreign_obj):
        assert COMPONENT not in api.labels(uid), api.get(uid)["metadata"]["name"]
    assert len(result["labelled"]) == len(mine)
    with open(own._record_file(p["state_file"])) as f:
        assert [json.loads(line)["component"] for line in f] == ["other"]


def test_u2_u3_killed_run_carries_then_rerun_labels(tmp_path):
    api, p = FakeApi(), params(tmp_path)
    own.snapshot(api, p, Module())
    own.begin(p, Module())
    deploy = api.add("Deployment", "web", "app")

    # U2: the run was killed in `app`; the next snapshot recovers it.
    result = own.snapshot(api, params(tmp_path, component="unused"), Module())
    state = json.loads((tmp_path / "ownership-uids.json").read_text())
    assert result["recovered"]["component"] == "app"
    assert result["labelled"] == []
    assert state["pending"] is None
    assert state["carry"] == {"app": [deploy]}
    assert COMPONENT not in api.labels(deploy)

    # U3: the rerun reports the carried Deployment as updated.
    own.begin(p, Module())
    record(p, type="object", uid=deploy, kind="Deployment", name="web", namespace="app", method="update")
    own.label(api, p, Module())
    state = json.loads((tmp_path / "ownership-uids.json").read_text())
    assert api.labels(deploy)[COMPONENT] == "app"
    assert state["carry"] == {}


def test_u4_teardown_leaves_pvcs_and_takes_the_certificate_secret(tmp_path):
    api, p = FakeApi(), params(tmp_path)
    mine = {f"{PREFIX}/env": "local", COMPONENT: "app"}
    api.add("StatefulSet", "db", "shared", labels=mine,
            spec={"volumeClaimTemplates": [{"metadata": {"name": "data"}}]})
    pvcs = [api.add("PersistentVolumeClaim", "data-db-%d" % i, "shared") for i in (0, 1)]
    api.add("Certificate", "tls", "shared", labels=mine, spec={"secretName": "tls-secret"})
    cert_secret = api.add("Secret", "tls-secret", "shared", annotations={"cert-manager.io/certificate-name": "tls"})
    unrelated = api.add("Secret", "unrelated", "shared")

    result = own.delete(api, p, Module())

    assert result["left"] == []
    assert not api.exists(cert_secret)
    assert all(api.exists(u) for u in pvcs + [unrelated])
    assert sorted(result["deleted"]) == ["Certificate shared/tls", "Secret shared/tls-secret", "StatefulSet shared/db"]


def test_failed_component_carries_unclaimed_objects(tmp_path):
    api, p = FakeApi(), params(tmp_path, component_failed=True)
    own.snapshot(api, p, Module())
    stray = api.add("Secret", "half-made", "app")
    own.label(api, p, Module())
    state = json.loads((tmp_path / "ownership-uids.json").read_text())
    assert state["carry"] == {"app": [stray]}

    # Without component_failed (a failure a rescue handled), nothing is carried.
    api2, p2 = FakeApi(), params(tmp_path / "x")
    (tmp_path / "x").mkdir()
    own.snapshot(api2, p2, Module())
    api2.add("Secret", "half-made", "app")
    own.label(api2, p2, Module())
    assert json.loads((tmp_path / "x" / "ownership-uids.json").read_text())["carry"] == {}


def test_named_record_matches_its_api_group(tmp_path):
    api, p = FakeApi(), params(tmp_path)
    own.snapshot(api, p, Module())
    core = api.add("Service", "web", "app", group="")
    knative = api.add("Service", "web", "app", group="serving.knative.dev")
    record(p, type="named", kind="Service", api_version="v1", name="web", namespace="app")
    own.label(api, p, Module())
    assert api.labels(core)[COMPONENT] == "app"
    assert COMPONENT not in api.labels(knative)


def test_helm_manifest_ref_matches_its_api_group(tmp_path):
    api, p = FakeApi(), params(tmp_path)
    own.snapshot(api, p, Module())
    helm_secret(api, "app", "app", 1, manifest=doc("Service", "web", "app"))
    core = api.add("Service", "web", "app", group="")
    knative = api.add("Service", "web", "app", group="serving.knative.dev")
    record(p, type="release", name="app", namespace="app")
    own.label(api, p, Module())
    assert api.labels(core)[COMPONENT] == "app"
    assert COMPONENT not in api.labels(knative)


def test_cli_record_without_a_group_is_the_core_group(tmp_path):
    api, p = FakeApi(), params(tmp_path)
    own.snapshot(api, p, Module())
    core = api.add("Service", "web", "app", group="")
    knative = api.add("Service", "web", "app", group="serving.knative.dev")
    record(p, type="cli", resource="service", name="web", namespace="app")
    record(p, type="cli", resource="service.serving.knative.dev", name="web", namespace="app")
    own.label(api, p, Module())
    assert api.labels(core)[COMPONENT] == "app"
    assert api.labels(knative)[COMPONENT] == "app"


@pytest.mark.parametrize("status", [403, 404, 405])
def test_list_skips_what_it_may_not_list(status):
    api = FakeApi()
    api.add("Secret", "s", "app")
    api.fail["/api/v1/secrets"] = status
    res = {"base": "/api/v1", "resource": "secrets"}
    assert own._list(api, res) == []


@pytest.mark.parametrize("status", [429, 500, 503])
def test_list_fails_on_other_errors(status):
    api = FakeApi()
    api.fail["/api/v1/secrets"] = status
    with pytest.raises(ApiException):
        own._list(api, {"base": "/api/v1", "resource": "secrets"})


def test_teardown_snapshot_without_a_baseline_writes_nothing(tmp_path):
    api, p = FakeApi(), params(tmp_path, teardown=True)
    result = own.snapshot(api, p, Module())
    assert result["baseline"] is False
    assert not (tmp_path / "ownership-uids.json").exists()
    # An install takes the baseline; a teardown then deletes by label.
    own.snapshot(api, params(tmp_path), Module())
    assert own.snapshot(api, p, Module())["baseline"] is True


def test_labels_added_to_an_existing_namespace_are_restored(tmp_path):
    api, p = FakeApi(), params(tmp_path)
    ns = api.add("Namespace", "customer", labels={"team": "a"})
    own.snapshot(api, p, Module())
    api.labels(ns)["istio.io/dataplane-mode"] = "ambient"
    record(p, type="object", uid=ns, kind="Namespace", name="customer", method="update")
    result = own.label(api, p, Module())
    assert COMPONENT not in api.labels(ns)
    assert result["recorded"] == ["Namespace customer: istio.io/dataplane-mode"]

    result = own.delete(api, p, Module())
    assert api.exists(ns)
    assert api.labels(ns) == {"team": "a"}
    assert result["restored"] == ["Namespace customer: istio.io/dataplane-mode"]
