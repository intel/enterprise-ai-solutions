# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
"""The SSO login-cookie settings: which platform, controller and route get them, and what is patched."""
import pathlib

import pytest
import yaml
from ansible_render import render

_ROLE = pathlib.Path(__file__).resolve().parents[3] / "roles" / "envoy_gateway"
_DEFAULTS = yaml.safe_load((_ROLE / "defaults" / "main.yaml").read_text())
_SSO = yaml.safe_load((_ROLE / "tasks" / "route_sso.yaml").read_text())
_CONTROLLER = yaml.safe_load((_ROLE / "tasks" / "controller.yaml").read_text())
_DETECT = yaml.safe_load((_ROLE / "tasks" / "detect.yaml").read_text())


def task(tasks, prefix):
    return next(t for t in tasks if t.get("name", "").startswith(prefix))


def value(raw, variables):
    """A rendered var; ansible-core < 2.19 renders containers as text, a play reads them back."""
    out = render(raw, variables) if isinstance(raw, str) else raw
    return yaml.safe_load(out) if isinstance(out, str) and out[:1] in "[{" else out


def resolve(var_block, variables):
    for name, raw in var_block.items():
        variables[name] = value(raw, variables) if isinstance(raw, str) else raw
    return variables


def sso(version="1.9.3", parents=(), managed=True, patch_policy=True, patches_on=True, create=True,
        state="present", current=None):
    """The block's and the patch task's vars, rendered in order, for one SSO route."""
    v = dict(_DEFAULTS, gateway_route_name="headlamp", gateway_route_namespace="headlamp",
             gateway_route_state=state, gateway_namespace="envoy-gateway-system", gateway_name="eg-gateway",
             gateway_create=create, _envoy_gateway_oidc_login_patches=patches_on,
             gateway_platform={"version": version, "controller_managed": managed,
                               "patch_policy_enabled": patch_policy},
             _eg_sso_route={"resources": [{"spec": {"parentRefs": list(parents)}}]},
             _eg_login_cur={"resources": [current] if current else []})
    v["_eg_version"] = value(_DEFAULTS["_eg_version"], v)
    block = task(_SSO, "envoy_gateway | sso | Login cookie patch for")
    resolve(block["vars"], v)
    patch = task(block["block"], "envoy_gateway | sso | Login cookie lifetime")
    op = patch["vars"]["_op"]
    v["_op"] = {**op, "value": render(op["value"], v)}
    return resolve({k: x for k, x in patch["vars"].items() if k != "_op"}, v)


def runs(v, when):
    return bool(value("{{ " + when + " }}", v))


LS = {"kind": "ListenerSet", "name": "headlamp", "namespace": "headlamp", "sectionName": "headlamp"}
GW = {"kind": "Gateway", "name": "eg-gateway", "namespace": "envoy-gateway-system", "sectionName": "https"}
OTHER = {"kind": "Gateway", "name": "other", "namespace": "envoy-gateway-system", "sectionName": "https"}


def test_per_route_patch_on_1_9_for_each_parent_of_this_gateway():
    v = sso(parents=[LS, GW, OTHER])
    assert [p["name"] for p in v["_patches"]] == [
        "envoy-gateway-system/eg-gateway/headlamp/headlamp/headlamp", "envoy-gateway-system/eg-gateway/https"]
    assert {p["type"] for p in v["_patches"]} == {"type.googleapis.com/envoy.config.route.v3.RouteConfiguration"}
    assert v["_patches"][0]["operation"]["value"] == "1800s"
    assert "httproute/headlamp/headlamp/" in v["_patches"][0]["operation"]["jsonPath"]


def test_listener_patch_on_1_7():
    v = sso(version="1.7.5", parents=[GW])
    assert [(p["type"].rsplit(".", 1)[-1], p["name"]) for p in v["_patches"]] == [
        ("Listener", "envoy-gateway-system/eg-gateway/https")]


def test_no_patch_for_a_route_on_another_gateway():
    assert sso(parents=[OTHER])["_patches"] == []
    assert sso(version="1.7.5", parents=[OTHER])["_patches"] == []


@pytest.mark.parametrize("change", [
    {"patches_on": False},  # OpenShift
    {"managed": False},  # a controller this platform did not install
    {"patch_policy": False},  # kept controller without EnvoyPatchPolicy
    {"create": False},  # a Gateway this platform did not create
    {"state": "absent"},
])
def test_no_patch_unless_ours_and_wanted(change):
    v = sso(parents=[GW], **change)
    assert not v["_wanted"] and v["_state"] == "absent"


OURS = {"metadata": {"labels": {"app.kubernetes.io/managed-by": "ai-solutions"}}}
FOREIGN = {"metadata": {"labels": {}}}


@pytest.mark.parametrize("current, wanted, applies, notice", [
    (None, True, True, False),  # create
    (OURS, True, True, False),  # update ours
    (OURS, False, True, False),  # delete ours
    (FOREIGN, True, False, True),  # someone else's: left alone
    (FOREIGN, False, False, True),
    (None, False, False, False),
])
def test_only_our_patch_is_created_updated_or_deleted(current, wanted, applies, notice):
    block = task(_SSO, "envoy_gateway | sso | Login cookie patch for")
    v = sso(parents=[GW], current=current, patches_on=wanted)
    assert runs(v, task(block["block"], "envoy_gateway | sso | Login cookie lifetime")["when"]) is applies
    assert runs(v, task(block["block"], "envoy_gateway | sso | Login cookie patch not ours")["when"]) is notice


@pytest.mark.parametrize("version, on, want", [("1.9.3", True, True), ("1.7.5", True, True),
                                                ("1.6.2", True, False), ("", True, False), ("1.9.3", False, False)])
def test_csrf_ttl_only_where_the_field_exists_and_patches_are_on(version, on, want):
    v = dict(_DEFAULTS, _envoy_gateway_oidc_login_patches=on, gateway_platform={"version": version})
    v["_eg_version"] = value(_DEFAULTS["_eg_version"], v)
    ttl = value(task(_SSO, "envoy_gateway | sso | {{")["vars"]["_csrf_ttl"], v)
    assert (ttl == {"oidc": {"csrfTokenTTL": "1800s"}}) is want


@pytest.mark.parametrize("version, managed, on, want", [
    ("1.7.5", True, True, True), ("1.9.3", True, True, False), ("1.7.5", False, True, False),
    ("1.7.5", True, False, False), ("", True, True, False)])
def test_cve_bootstrap_only_on_our_1_7(version, managed, on, want):
    proxy = task(_CONTROLLER, "envoy_gateway | Create EnvoyProxy config")
    v = dict(_DEFAULTS, _envoy_gateway_oidc_login_patches=on,
             gateway_platform={"version": version, "controller_managed": managed})
    v["_eg_version"] = value(_DEFAULTS["_eg_version"], v)
    v["_eg_runtime_patches"] = proxy["vars"]["_eg_runtime_patches"]
    assert ("bootstrap" in value(proxy["vars"]["_eg_bootstrap"], v)) is want


def detect_vars(cur, latest="1.9.3", action="keep", managed=True, cfg=None):
    rules = task(_DETECT, "envoy_gateway | detect | Decide")["vars"]
    v = dict(_DEFAULTS, _envoy_gateway_oidc_login_patches=True, _managed=managed, _cur=cur, _latest=latest,
             _action=action, _cfg=cfg or {}, _ctrl_ns="envoy-gateway-system",
             _det_ctrl={"release": "envoy-gateway"})
    v["_patch_policy"] = value(rules["_patch_policy"], v)
    notice = next(r for r in rules["_notice_rules"] if "EnvoyPatchPolicy is off" in r["msg"])
    return v, notice


@pytest.mark.parametrize("action, cfg, want", [
    ("install", None, True), ("upgrade", None, True), ("keep", None, False),
    ("keep", {"extensionApis": {"enableEnvoyPatchPolicy": True}}, True)])
def test_patch_policy_on_after_our_install_or_when_the_controller_has_it(action, cfg, want):
    assert bool(detect_vars("1.9.3", action=action, cfg=cfg)[0]["_patch_policy"]) is want


def test_notice_names_the_fix_that_works():
    v, notice = detect_vars("1.7.5")
    assert value(notice["show"], v)
    assert "envoy_gateway_upgrade: true upgrades it to 1.9.3" in render(notice["msg"], v)
    v, notice = detect_vars("1.9.3")
    msg = render(notice["msg"], v)
    assert "envoy_gateway_upgrade" not in msg
    assert "helm upgrade envoy-gateway -n envoy-gateway-system --reuse-values" in msg
