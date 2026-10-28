# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for filter_plugins/upgrade_checks.py (no cluster)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "filter_plugins"))

from upgrade_checks import (  # noqa: E402
    check_layers,
    claim_layer_dirs,
    classify_run,
    layer_fully_planned,
    layer_record_is_legacy,
    layer_release_version,
    lift_layer_record,
    record_dependencies,
    report_due,
    upgrade_gate,
    version_change_verdict,
)

ERAG_LEGACY = {
    "deployment": {
        "version": "3.0.0", "targetVersion": None, "imageTag": "3.0.0", "targetImageTag": "3.0.0",
        "status": "complete", "startedAt": "2026-10-20T10:00:00Z", "completedAt": "2026-10-20T10:40:00Z",
        "installedBy": "ansible",
    },
    "components": {"edp": {"status": "complete", "appVersion": "3.0.0"}},
}


def record(version="1.1.0", status="complete", target=None):
    return {"layer": "platform", "version": version, "status": status, "targetVersion": target}


@pytest.mark.parametrize("rec, release, expected", [
    ({}, "1.1.0", "fresh"),
    (record(), "1.1.0", "reinstall"),
    (record(status="in-progress", target="1.2.0"), "1.2.0", "resume"),
    (record(status="halted", target="1.2.0"), "1.2.0", "resume"),
    (record(), "1.2.0", "upgrade"),
    (record(version="1.2.0"), "1.1.0", "downgrade"),
    (record(status="in-progress", target="1.2.0"), "1.3.0", "conflict"),
    (record(status="halted", target="1.2.0"), "1.1.0", "conflict"),
    (record(status="deleting"), "1.1.0", "conflict"),
    (record(status="deleting"), None, "conflict"),
    ({}, None, "unversioned"),
    (record(), None, "unversioned"),
    (record(version=None, status="complete"), "1.1.0", "fresh"),
    (record(version="1.1.0+build.7"), "1.1.0", "reinstall"),
])
def test_classify_run(rec, release, expected):
    assert classify_run(rec, release, "platform") == expected


def test_classify_run_names_the_layer_of_a_bad_version():
    with pytest.raises(ValueError, match="layer platform: 'not-a-version'"):
        classify_run(record(version="not-a-version"), "1.1.0", "platform")


def test_legacy_erag_record_lifted_on_read():
    assert layer_record_is_legacy(ERAG_LEGACY)
    assert lift_layer_record(ERAG_LEGACY) == {
        "version": "3.0.0", "targetVersion": None, "status": "complete",
        "startedAt": "2026-10-20T10:00:00Z", "completedAt": "2026-10-20T10:40:00Z",
    }
    assert classify_run(lift_layer_record(ERAG_LEGACY), "3.0.0", "erag") == "reinstall"
    assert classify_run(lift_layer_record(ERAG_LEGACY), "3.1.0", "erag") == "upgrade"


def test_core_record_not_legacy_and_kept():
    rec = dict(record(), dependencies={"infrastructure": "1.1.0"}, stray="dropped",
               installedBy={"installerVersion": "1.1.0", "installerCommit": ""})
    assert not layer_record_is_legacy(rec)
    assert not layer_record_is_legacy({})
    lifted = lift_layer_record(rec)
    assert "stray" not in lifted
    assert lifted["dependencies"] == {"infrastructure": "1.1.0"}
    assert lifted["installedBy"]["installerVersion"] == "1.1.0"


@pytest.mark.parametrize("data, expected", [
    ({"layer_version": "1.1.0"}, "1.1.0"),
    ({"solution_version": "3.0.0"}, "3.0.0"),
    ({"layer_version": "1.2.0", "solution_version": "3.0.0"}, "1.2.0"),
    ({"schema": 1}, None),
    (None, None),
])
def test_layer_release_version(data, expected):
    assert layer_release_version(data) == expected


@pytest.mark.parametrize("run_class, verdict", [
    ("fresh", "pass"), ("reinstall", "pass"), ("resume", "pass"), ("upgrade", "pass"),
    ("unversioned", "skipped"), ("downgrade", "blocking"), ("conflict", "blocking"),
])
def test_version_change_verdict(run_class, verdict):
    v = version_change_verdict(run_class, "erag", record(version="3.1.0", target="3.2.0"), "3.0.0", "dev")
    assert v["verdict"] == verdict
    assert v["check"] == "version-change" and v["layer"] == "erag"
    if verdict != "pass":
        assert v["reason"]
    if verdict == "blocking":
        assert v["resolve"]


def test_conflict_on_deleting_names_the_teardown():
    v = version_change_verdict("conflict", "erag", record(status="deleting"), "3.0.0", "dev")
    assert v["resolve"] == "./es_auto_installer.sh teardown erag --env dev"


DOWNGRADE = version_change_verdict("downgrade", "erag", record(version="3.1.0"), "3.0.0")
PASS = version_change_verdict("reinstall", "platform", record(), "1.1.0")


def test_blocking_is_advisory_without_supports_upgrade():
    gate = upgrade_gate([DOWNGRADE, PASS], {"erag": False})
    assert gate["refused"] == [] and gate["forced"] is False
    assert [c["gating"] for c in gate["checks"]] == [False, False]


def test_blocking_refuses_with_supports_upgrade():
    gate = upgrade_gate([DOWNGRADE, PASS], {"erag": True, "platform": False})
    assert gate["refused"] == ["erag/version-change"]
    assert gate["forced"] is False and gate["forcedChecks"] == []
    assert [c["gating"] for c in gate["checks"]] == [True, False]


def test_forced_bypass_is_recorded():
    gate = upgrade_gate([DOWNGRADE], {"erag": True}, forced=True)
    assert gate["refused"] == []
    assert gate["forced"] is True and gate["forcedChecks"] == ["erag/version-change"]


def test_force_without_a_gating_verdict_is_not_forced():
    gate = upgrade_gate([DOWNGRADE, PASS], {"erag": False}, forced=True)
    assert gate["forced"] is False and gate["forcedChecks"] == []


@pytest.mark.parametrize("run_class, due", [
    ("upgrade", True), ("downgrade", True), ("fresh", False), ("reinstall", False),
    ("resume", False), ("conflict", False), ("unversioned", False),
])
def test_report_only_for_upgrade_and_downgrade(run_class, due):
    assert report_due(run_class) is due


def test_record_dependencies():
    records = {"platform": record(version="1.1.0"), "inference": {}}
    assert record_dependencies(records, ["platform", "inference", "infrastructure"]) == {
        "platform": "1.1.0", "inference": None, "infrastructure": None}
    assert record_dependencies(records, None) == {}


def test_only_every_class_checks_judge_a_fresh_install():
    classes = {"infrastructure": "fresh", "platform": "upgrade", "erag": "downgrade", "inference": "reinstall"}
    every_class = ["version_change"]
    assert check_layers(classes, "version_change", every_class) == list(classes)
    assert check_layers(classes, "backup", every_class) == ["platform", "erag"]
    assert check_layers({"platform": "fresh"}, "backup", every_class) == []


def test_claim_layer_dirs():
    erag = {"layers": [{"name": "erag"}], "components": [{"name": "edp", "layer": "erag"}]}
    inference = {"components": [{"name": "vllm", "layer": "inference"}]}
    dirs = claim_layer_dirs({}, erag, "ext/erag/deployment")
    assert dirs == {"erag": "ext/erag/deployment"}
    dirs = claim_layer_dirs(dirs, inference, "ext/ai-inference")
    assert dirs == {"erag": "ext/erag/deployment", "inference": "ext/ai-inference"}
    # an earlier component-only claim yields to a later declaration, never to a later component
    assert claim_layer_dirs(dirs, {"components": [{"name": "x", "layer": "inference"}]}, "ext/other")["inference"] == "ext/ai-inference"
    assert claim_layer_dirs(dirs, {"layers": [{"name": "inference"}]}, "ext/other")["inference"] == "ext/other"
    assert claim_layer_dirs(dirs, None, "ext/empty") == dirs


@pytest.mark.parametrize("planned, expected", [
    (["istio", "metallb"], True), (["istio", "metallb", "vllm"], True), (["istio"], False), ([], False),
])
def test_layer_fully_planned(planned, expected):
    assert layer_fully_planned(["istio", "metallb"], planned) is expected
