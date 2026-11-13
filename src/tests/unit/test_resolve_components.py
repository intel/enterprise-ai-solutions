# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import importlib.util
import pathlib

import pytest
import yaml

_PLUGIN = pathlib.Path(__file__).resolve().parents[3] / "filter_plugins" / "resolve_components.py"
_spec = importlib.util.spec_from_file_location("resolve_components", _PLUGIN)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

# Layers declared out of order: execution order must come from depends_on.
LAYERS = [
    {"name": "top", "depends_on": ["mid", "opt"]},
    {"name": "base", "depends_on": []},
    {"name": "mid", "depends_on": ["base"]},
    {"name": "opt", "enabled": "False", "depends_on": ["base"]},
]
COMPONENTS = [
    {"name": "storage", "layer": "base", "depends_on": ["k8s"]},
    {"name": "k8s", "layer": "base"},
    {"name": "fabric", "layer": "base", "enabled": "False"},
    {"name": "gateway", "layer": "mid", "aliases": ["envoy_gateway"], "depends_on": ["certs"]},
    {"name": "certs", "layer": "mid", "enabled": "yes"},
    {"name": "auth", "layer": "mid", "depends_on": ["envoy_gateway"]},
    {"name": "app", "layer": "top"},
    {"name": "extra", "layer": "opt"},
]


def resolve(target, **kwargs):
    return [c["name"] for c in _mod.resolve_components(COMPONENTS, LAYERS, target, **kwargs)]


@pytest.mark.parametrize(("target", "kwargs", "expected"), [
    ("base", {}, ["k8s", "storage"]),
    ("base", {"action": "validate"}, ["k8s", "storage"]),
    ("top", {}, ["k8s", "storage", "certs", "gateway", "auth", "app"]),
    ("opt", {}, ["k8s", "storage", "extra"]),
    ("mid", {"include_deps": False}, ["certs", "gateway", "auth"]),
    ("envoy_gateway", {}, ["certs", "gateway"]),
    ("auth", {"include_deps": False}, ["auth"]),
    ("gateway", {"action": "teardown", "include_deps": False}, ["gateway"]),
    ("app, certs", {}, ["certs", "app"]),
    ("base,app", {}, ["k8s", "storage", "app"]),
    ("top", {"skip": "storage,mid"}, ["k8s", "app"]),
    ("mid", {"skip": "envoy_gateway"}, ["k8s", "storage", "certs", "auth"]),
    ("gateway", {"action": "teardown"}, ["auth", "gateway"]),
    ("base", {"action": "teardown"}, ["app", "extra", "auth", "gateway", "certs", "storage", "k8s"]),
])
def test_resolve_components(target, kwargs, expected):
    assert resolve(target, **kwargs) == expected


@pytest.mark.parametrize(("components", "target", "action", "message"), [
    (COMPONENTS, "base", "bogus", "Invalid action 'bogus'"),
    (COMPONENTS, "nope", "install", "Unknown target 'nope'"),
    (COMPONENTS, " , ", "install", "target is required"),
    (COMPONENTS, "\t,", "install", "target is required"),
    (COMPONENTS, None, "install", "target is required"),
    ([{"layer": "base"}], "base", "install", "needs a name"),
])
def test_bad_arguments_fail_clearly(components, target, action, message):
    with pytest.raises(ValueError, match=message):
        _mod.resolve_components(components, LAYERS, target, action)


@pytest.mark.parametrize(("layers", "components"), [
    (LAYERS, [{"name": "a", "layer": "base", "depends_on": ["b"]},
              {"name": "b", "layer": "base", "depends_on": ["a"]}]),
    ([{"name": "x", "depends_on": ["y"]}, {"name": "y", "depends_on": ["x"]}],
     [{"name": "a", "layer": "x"}, {"name": "b", "layer": "y"}]),
])
def test_cycle_warns_and_keeps_every_component(monkeypatch, layers, components):
    warned = []
    monkeypatch.setattr(_mod.display, "warning", warned.append)
    assert len(_mod.resolve_components(components, layers, components[0]["layer"])) == 2
    assert "Cycle detected" in warned[0]


def test_enabled_none_counts_as_enabled():
    assert _mod.resolve_components([{"name": "a", "layer": "base", "enabled": None}], LAYERS, "base")


def test_disabled_components_and_component_deps():
    assert _mod.disabled_components(COMPONENTS, LAYERS, "base") == ["fabric"]
    assert _mod.component_deps(COMPONENTS, "auth") == ["certs", "gateway"]
    assert _mod.component_deps(COMPONENTS, "k8s") == []


def test_filters_are_registered():
    assert set(_mod.FilterModule().filters()) == {
        "resolve_components", "disabled_components", "component_deps"}


# The real registry. enabled: is a Jinja expression there, so every component counts as enabled.
_REGISTRY = yaml.safe_load((_PLUGIN.parents[1] / "configs" / "components.yaml").read_text())


def resolve_registry(target, action):
    components = [dict(c, enabled=True) for c in _REGISTRY["components"]]
    return [c["name"] for c in _mod.resolve_components(components, _REGISTRY["layers"], target, action=action)]


def test_teardown_inference_keeps_nri_cpu_balloons():
    assert "nri_cpu_balloons" not in resolve_registry("inference", "teardown")
    assert "nri_cpu_balloons" in resolve_registry("platform", "teardown")


def test_install_platform_runs_nri_cpu_balloons_before_the_platform_services():
    order = [n for n in resolve_registry("platform", "install")
             if n in {c["name"] for c in _REGISTRY["components"] if c["layer"] == "platform"}]
    assert set(order[:order.index("nri_cpu_balloons")]) == {"node_feature_discovery", "nvidia_gpu_operator"}
