# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the used_by handling of the resolve_components filter plugin."""

from plugin_loader import load_plugin

rc = load_plugin("resolve_components")

LAYERS = [
    {"name": "platform"},
    {"name": "inference", "depends_on": ["platform"]},
]


def comp(name, layer="platform", enabled=True, **extra):
    return dict({"name": name, "layer": layer, "enabled": enabled}, **extra)


def fixture(obs=True, lf=True, store=True):
    """seaweedfs is shared by observability (platform) and langfuse (inference)."""
    return [
        comp("seaweedfs", enabled=store, used_by=["observability", "langfuse"]),
        comp("observability", enabled=obs, depends_on=["seaweedfs"]),
        comp("langfuse", layer="inference", enabled=lf, depends_on=["seaweedfs"]),
    ]


def names(components):
    return [c["name"] for c in components]


def install(components, target="platform,inference"):
    return names(rc.resolve_components(components, LAYERS, target))


def teardown(components, target, **kwargs):
    return names(rc.resolve_components(components, LAYERS, target, action="teardown", **kwargs))


def test_used_by_active_while_a_user_is_enabled():
    assert install(fixture(obs=False)) == ["seaweedfs", "langfuse"]
    assert install(fixture(lf=False)) == ["seaweedfs", "observability"]


def test_used_by_inactive_without_enabled_users():
    assert install(fixture(obs=False, lf=False)) == []


def test_used_by_stays_off_when_its_own_enabled_is_false():
    assert "seaweedfs" not in install(fixture(store=False))


def test_used_by_ignores_users_that_are_not_declared():
    components = [comp("store", used_by=["ghost"])]
    assert install(components) == []


def test_used_by_enabled_strings_are_coerced():
    assert install(fixture(obs="False", lf="True")) == ["seaweedfs", "langfuse"]


def test_teardown_gate_uses_own_enabled():
    # Nothing uses it any more, but an env installed before still gets it removed.
    assert "seaweedfs" in teardown(fixture(obs=False, lf=False), "platform", include_deps=False)


def test_teardown_user_takes_used_by_along():
    # Neither user stays: tearing down observability alone removes seaweedfs.
    components = fixture(lf=False)
    assert teardown(components, "observability", include_deps=False) == ["observability", "seaweedfs"]


def test_teardown_keeps_used_by_while_another_user_stays():
    got = teardown(fixture(), "observability", include_deps=False)
    assert got == ["observability"]


def test_teardown_pulls_in_used_by_from_unreached_layer():
    # langfuse's layer is targeted, seaweedfs (platform) is not reached through layers.
    components = fixture(obs=False)
    got = teardown(components, "langfuse", include_deps=False)
    assert got == ["langfuse", "seaweedfs"]


def test_teardown_staying_user_disabled_by_string_does_not_keep_it():
    components = fixture(lf="False")
    assert teardown(components, "observability", include_deps=False) == ["observability", "seaweedfs"]


def test_teardown_skip_removes_used_by_again():
    got = teardown(fixture(lf=False), "observability", include_deps=False, skip="seaweedfs")
    assert got == ["observability"]


def test_kept_components_names_the_staying_users():
    components = fixture()
    active = rc.resolve_components(components, LAYERS, "observability", action="teardown", include_deps=False)
    assert rc.kept_components(components, active) == {"seaweedfs": ["langfuse"]}


def test_kept_components_empty_when_taken_along():
    components = fixture(lf=False)
    active = rc.resolve_components(components, LAYERS, "observability", action="teardown", include_deps=False)
    assert rc.kept_components(components, active) == {}


def test_kept_components_empty_when_no_user_is_planned():
    components = fixture()
    assert rc.kept_components(components, [comp("unrelated")]) == {}


def test_kept_components_ignores_disabled_used_by():
    components = fixture(store=False)
    active = [comp("observability")]
    assert rc.kept_components(components, active) == {}


def test_kept_components_lists_only_enabled_staying_users():
    components = [
        comp("store", used_by=["a", "b", "c"]),
        comp("a"), comp("b"), comp("c", enabled=False),
    ]
    assert rc.kept_components(components, [comp("a")]) == {"store": ["b"]}
