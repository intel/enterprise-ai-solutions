# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for filter_plugins/upgrade_checks.py (no cluster)."""

import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "filter_plugins"))

from upgrade_checks import (  # noqa: E402
    check_layers,
    claim_layer_dirs,
    classify_run,
    data_consistency_present_verdict,
    layer_fully_planned,
    layer_record_is_legacy,
    record_owned_by_layer,
    layer_release_version,
    lift_layer_record,
    record_dependencies,
    record_forced,
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


def test_legacy_record_left_to_its_layer_until_it_declares_supports_upgrade():
    assert record_owned_by_layer(ERAG_LEGACY, False)
    assert not record_owned_by_layer(ERAG_LEGACY, True)
    assert not record_owned_by_layer(record(), False)
    assert not record_owned_by_layer({}, False)


def test_core_record_not_legacy_and_kept():
    rec = dict(record(), dependencies={"infrastructure": "1.1.0"}, stray="dropped",
               haltReason="component-failed",
               installedBy={"installerVersion": "1.1.0", "installerCommit": ""})
    assert not layer_record_is_legacy(rec)
    assert not layer_record_is_legacy({})
    lifted = lift_layer_record(rec)
    assert "stray" not in lifted
    assert lifted["dependencies"] == {"infrastructure": "1.1.0"}
    assert lifted["installedBy"]["installerVersion"] == "1.1.0"
    assert lifted["haltReason"] == "component-failed"


def test_record_write_replaces_the_configmap():
    # A merge patch would keep the labels of a legacy record core takes over.
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "roles", "upgrade_checks", "tasks", "record.yaml")
    with open(path) as f:
        tasks = yaml.safe_load(f)
    block = next(t for t in tasks if "block" in t)["block"]
    write = next(t for t in block if t["kubernetes.core.k8s"]["state"] == "present")
    assert write["kubernetes.core.k8s"]["force"] is True
    assert write["kubernetes.core.k8s"]["definition"]["metadata"]["labels"] == "{{ upgrade_record_labels }}"


def _tasks(tasks):
    for task in tasks:
        yield task
        for key in ("block", "rescue", "always"):
            yield from _tasks(task.get(key, []))


@pytest.mark.parametrize("consistency_halt", [True, False])
def test_halted_write_never_passes_omit_as_the_halt_reason(consistency_halt):
    # omit is only valid as a module argument; in include_role vars it reaches record.yaml as a string.
    jinja2 = pytest.importorskip("jinja2")
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "playbooks", "includes", "run_layer.yaml")
    with open(path) as f:
        tasks = yaml.safe_load(f)
    write = next(t for t in _tasks(tasks) if t.get("name") == "Record {{ layer }} as halted")
    omit = "__omit_place_holder__"
    reason = jinja2.Template(write["vars"]["upgrade_record_halt_reason"]).render(
        _layer_consistency_halt=consistency_halt, omit=omit)
    assert omit not in reason
    assert reason == ("data-consistency" if consistency_halt else "")


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


def test_data_consistency_present_passes_when_the_file_is_there():
    verdict = data_consistency_present_verdict("erag", True, True)
    assert (verdict["verdict"], verdict["check"]) == ("pass", "data-consistency-present")
    assert verdict["resolve"] is None


def test_data_consistency_present_blocks_a_gating_layer_with_no_file():
    verdict = data_consistency_present_verdict("erag", True, False)
    assert verdict["verdict"] == "blocking"
    assert verdict["reason"] == "erag declares supports.upgrade but ships no data_consistency.yaml"
    assert "data_consistency.yaml" in verdict["resolve"] and "--force" in verdict["resolve"]


def test_data_consistency_present_is_skipped_without_supports_upgrade():
    assert data_consistency_present_verdict("inference", False, False)["verdict"] == "skipped"
    assert data_consistency_present_verdict("inference", False, True)["verdict"] == "skipped"


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


def test_resume_inherits_the_interrupted_records_forced_checks():
    raw = {"forcedChecks": ["erag/version-change"]}
    assert record_forced(raw, ["erag/backup-freshness"], "resume", "erag") == [
        "erag/backup-freshness", "erag/version-change"]


def test_resume_of_a_non_forced_record_stays_unforced():
    assert record_forced({}, [], "resume", "erag") == []
    assert record_forced({"forcedChecks": []}, [], "resume", "erag") == []


def test_non_resume_ignores_the_old_records_forced():
    raw = {"forcedChecks": ["erag/version-change"]}
    assert record_forced(raw, ["erag/backup-freshness"], "upgrade", "erag") == ["erag/backup-freshness"]
    assert record_forced(raw, [], "fresh", "erag") == []


def test_record_forced_drops_other_layers_checks():
    raw = {"forcedChecks": ["erag/version-change", "platform/version-change"]}
    forced = record_forced(raw, ["erag/backup-freshness", "platform/backup-freshness"], "resume", "erag")
    assert forced == ["erag/backup-freshness", "erag/version-change"]


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


# Data consistency
from upgrade_checks import (  # noqa: E402
    compare_consistency,
    consistency_counts,
    consistency_failed,
    consistency_merge,
    consistency_verdict,
)

DISABLED = {"skipped": "chat history disabled (chat_history_enabled: false)"}


class TestConsistencyCounts:
    def test_counts_and_skipped_stores_are_kept(self):
        assert consistency_counts({"vdb": 5, "edp": "7", "chat": DISABLED}, "count.yaml") == {
            "vdb": 5, "edp": 7, "chat": DISABLED}

    @pytest.mark.parametrize("value", [-1, True, "many", None, {"skipped": ""}, {"count": 3}])
    def test_a_value_that_is_no_count_is_skipped_naming_the_probe(self, value):
        reason = consistency_counts({"vdb": value}, "count.yaml")["vdb"]["skipped"]
        assert reason.startswith("probe count.yaml returned")

    def test_a_result_that_is_no_mapping_is_one_skipped_store(self):
        assert list(consistency_counts("5", "count.yaml")) == ["count.yaml"]
        assert "not a mapping" in consistency_counts(None, "count.yaml")["count.yaml"]["skipped"]

    def test_a_failed_probe_is_one_skipped_store(self):
        assert consistency_failed("count.yaml", "Job timed out") == {
            "count.yaml": {"skipped": "probe count.yaml failed: Job timed out"}}

    def test_a_store_two_probes_report_is_skipped_naming_the_second(self):
        merged = consistency_merge(consistency_merge({}, {"vdb": 5}, "a.yaml"), {"vdb": 6, "edp": 1}, "b.yaml")
        assert merged == {"vdb": {"skipped": "store reported by more than one probe, again by b.yaml"}, "edp": 1}


class TestCompareConsistency:
    @pytest.mark.parametrize("pre, post, verdict", [(5, 7, "pass"), (5, 5, "pass"), (5, 4, "blocking"), (0, 0, "baseline")])
    def test_counted_store(self, pre, post, verdict):
        assert compare_consistency({"vdb": pre}, {"vdb": post}) == {"vdb": {"pre": pre, "post": post, "verdict": verdict}}

    def test_a_skipped_store_carries_the_reason_and_its_uncounted_side_is_null(self):
        assert compare_consistency({"chat": 4}, {"chat": DISABLED}) == {
            "chat": {"pre": 4, "post": None, "verdict": "skipped", "reason": DISABLED["skipped"]}}

    def test_a_store_counted_on_one_side_only_is_skipped(self):
        stores = compare_consistency({"vdb": 2}, {"edp": 1})
        assert stores["vdb"]["reason"] == "not counted after the run"
        assert stores["edp"]["reason"] == "not counted before the run"


class TestConsistencyVerdict:
    def test_a_layer_without_probes_is_skipped_naming_the_file(self):
        verdict = consistency_verdict(None, {}, "erag", True)
        assert (verdict["verdict"], verdict["gating"]) == ("skipped", False)
        assert verdict["reason"] == "erag ships no data_consistency.yaml"

    def test_lost_entries_block_and_gate_a_supports_upgrade_layer(self):
        verdict = consistency_verdict({"vdb": 5, "chat": DISABLED}, {"vdb": 4, "chat": DISABLED}, "erag", True)
        assert (verdict["verdict"], verdict["gating"], verdict["check"]) == ("blocking", True, "data-consistency")
        assert verdict["reason"] == "entries lost: vdb: 5 -> 4"

    def test_lost_entries_on_a_layer_without_supports_upgrade_do_not_gate(self):
        verdict = consistency_verdict({"vdb": 5}, {"vdb": 4}, "inference", False)
        assert (verdict["verdict"], verdict["gating"]) == ("blocking", False)

    def test_only_skipped_stores_is_skipped_never_a_baseline(self):
        assert consistency_verdict({"chat": DISABLED}, {"chat": DISABLED}, "erag")["verdict"] == "skipped"
        assert consistency_verdict({}, {}, "erag")["verdict"] == "skipped"

    def test_every_counted_store_empty_is_a_baseline(self):
        assert consistency_verdict({"vdb": 0, "chat": DISABLED}, {"vdb": 0, "chat": DISABLED}, "erag")["verdict"] == "baseline"

    def test_an_empty_store_beside_a_kept_one_passes(self):
        verdict = consistency_verdict({"vdb": 0, "edp": 3}, {"vdb": 0, "edp": 3}, "erag", True)
        assert (verdict["verdict"], verdict["gating"]) == ("pass", False)
