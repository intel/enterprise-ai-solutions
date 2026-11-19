# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for filter_plugins/upgrade_checks.py (no cluster)."""

import os
import subprocess
import sys
import textwrap

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "filter_plugins"))

from upgrade_checks import (  # noqa: E402
    check_layers,
    claim_layer_dirs,
    classify_run,
    cluster_probe_verdict,
    layer_fully_planned,
    layer_record_is_legacy,
    record_owned_by_layer,
    layer_release_version,
    lift_layer_record,
    record_dependencies,
    record_forced,
    record_version,
    report_due,
    upgrade_gate,
    upgrade_path_verdict,
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


def _task_file(path):
    """Load a task file by its path from the repository root."""
    with open(os.path.join(os.path.dirname(__file__), "..", "..", "..", path)) as f:
        return yaml.safe_load(f)


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
    (record(status="deleting"), "3.1.0-SNAPSHOT", "conflict"),
    (record(status="halted", target="not-a-version"), "1.2.0", "conflict"),
    (record(status="in-progress", target="not-a-version"), "1.2.0", "conflict"),
    ({}, None, "unversioned"),
    (record(), None, "unversioned"),
    (record(version=None, status="complete"), "1.1.0", "fresh"),
    (record(version="1.1.0+build.7"), "1.1.0", "reinstall"),
])
def test_classify_run(rec, release, expected):
    assert classify_run(rec, release, "platform") == expected


def test_installer_layers_never_look_up_a_version_file(tmp_path):
    from ansible.plugins.filter.core import FilterModule as CoreFilters
    from jinja2 import Environment

    (tmp_path / "version.yaml").write_text("solution_version: 3.1.0\n")
    task = next(t for t in _task_file("roles/upgrade_checks/tasks/run.yaml")
                if t["name"] == "upgrade_checks | Classify and check the install")
    resolve = next(t for t in task["block"] if t["name"].endswith("Resolve each planned layer's release version"))
    env = Environment()
    env.filters.update(CoreFilters().filters())
    env.tests["file"] = os.path.isfile
    template = env.from_string(resolve["vars"]["_version_data"].strip())

    def render(layer):
        return template.render(item=layer, upgrade_installer_layers=["infrastructure", "platform"],
                               _version_file=str(tmp_path / "version.yaml"),
                               lookup=lambda _plugin, path: open(path).read())

    assert render("platform") == "{}"
    assert render("erag") == "{'solution_version': '3.1.0'}"


def test_classify_run_does_not_crash_on_an_unparseable_target_version():
    # An unfinished record's target that does not parse is a conflict, not a preflight crash.
    rec = {"status": "halted", "targetVersion": "not-a-version", "version": "1.0.0"}
    assert classify_run(rec, "2.0.0", "inference") == "conflict"


def test_classify_run_names_the_layer_of_a_bad_version():
    with pytest.raises(ValueError, match="layer platform: 'not-a-version'"):
        classify_run(record(version="not-a-version"), "1.1.0", "platform")


@pytest.mark.parametrize("rec", [{}, record(version=None), record(status="halted", target="3.1.0")])
def test_classify_run_refuses_a_release_that_is_not_a_version(rec):
    # Recorded once, such a release would fail every later run of the same version.
    with pytest.raises(ValueError, match="layer platform: '3.1.0-SNAPSHOT' is not a version"):
        classify_run(rec, "3.1.0-SNAPSHOT", "platform")


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
    tasks = _task_file("roles/upgrade_checks/tasks/record.yaml")
    block = next(t for t in tasks if t["name"].endswith("Write the {{ upgrade_record_layer }} record"))["block"]
    write = next(t for t in block if t["kubernetes.core.k8s"]["state"] == "present")
    assert write["kubernetes.core.k8s"]["force"] is True
    assert write["kubernetes.core.k8s"]["definition"]["metadata"]["labels"] == "{{ upgrade_record_labels }}"


def test_record_write_runs_for_deleting_with_no_prior_record():
    # A halted teardown of a layer core never recorded must still leave a marker.
    tasks = _task_file("roles/upgrade_checks/tasks/record.yaml")
    block_task = next(t for t in tasks if t["name"].endswith("Write the {{ upgrade_record_layer }} record"))
    status_cond = next(c for c in block_task["when"] if "upgrade_record_status in" in c)
    assert "deleting" in status_cond


@pytest.mark.parametrize("given, component, expected", [
    ("data-consistency", None, "data-consistency"),
    ("data-consistency", "vllm", "data-consistency"),
    (None, "vllm", "component-failed"),
    (None, None, "None"),
])
def test_halted_write_keeps_the_reason_the_caller_gives(given, component, expected):
    # The resume refusal after a data-consistency halt reads this reason.
    from jinja2 import Environment

    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "roles", "upgrade_checks", "tasks", "record.yaml")
    with open(path) as f:
        tasks = yaml.safe_load(f)
    task_vars = next(t for t in tasks if t["name"].endswith("Write the {{ upgrade_record_layer }} record"))["vars"]
    env = Environment()
    ctx = {k: v for k, v in (("upgrade_record_halt_reason", given), ("_running_component", component)) if v}
    ctx["_halt_reason_default"] = env.from_string(task_vars["_halt_reason_default"]).render(**ctx)
    if ctx["_halt_reason_default"] == "None":
        ctx["_halt_reason_default"] = None
    template = task_vars["_changes"]["halted"]["haltReason"]
    assert env.from_string(template).render(**ctx) == expected


def test_supports_upgrade_is_coerced_once_for_gate_and_record():
    # A templated declaration renders as the string "False"; it must not gate.
    from ansible.plugins.filter.core import FilterModule as CoreFilters
    from ansible.plugins.filter.mathstuff import FilterModule as MathFilters
    from jinja2 import Environment

    tasks = _task_file("roles/upgrade_checks/tasks/run.yaml")
    task = next(t for t in tasks if t["name"].endswith("Note which layers declare supports.upgrade"))
    env = Environment()
    env.filters.update(CoreFilters().filters(), **MathFilters().filters())
    layers = [{"name": "platform", "supports": {"upgrade": "False"}},
              {"name": "erag", "supports": {"upgrade": "True"}},
              {"name": "inference"}]
    rendered = env.from_string(task["ansible.builtin.set_fact"]["_layer_supports_upgrade"].strip()).render(
        layers=layers)
    supports = yaml.safe_load(rendered.replace("'", '"'))
    assert supports == {"platform": False, "erag": True, "inference": False}
    blocking = [dict(DOWNGRADE, layer=name) for name in supports]
    assert upgrade_gate(blocking, supports)["refused"] == ["erag/version-change"]


@pytest.mark.parametrize("planned, refuses", [(["platform", "erag"], True), (["platform"], False)])
def test_probe_warning_names_the_refusal_only_with_a_gating_layer_planned(planned, refuses):
    from ansible.plugins.filter.core import FilterModule as CoreFilters
    from ansible.plugins.filter.mathstuff import FilterModule as MathFilters
    from jinja2 import Environment

    task = next(t for t in _task_file("roles/upgrade_checks/tasks/run.yaml")
                if t["name"].endswith("did not answer"))
    env = Environment()
    env.filters.update(CoreFilters().filters(), **MathFilters().filters())
    msg = env.from_string(task["ansible.builtin.debug"]["msg"]).render(
        kubernetes_kubeconfig="/k", _record_probe={"msg": "timeout"},
        _active_components=[{"name": n, "layer": n} for n in planned],
        _layer_supports_upgrade={"platform": False, "erag": True})
    assert ("still refuses this run" in msg) == refuses


@pytest.mark.parametrize("data, expected", [
    ({"layer_version": "1.1.0"}, "1.1.0"),
    ({"solution_version": "3.0.0"}, "3.0.0"),
    ({"layer_version": "1.2.0", "solution_version": "3.0.0"}, "1.2.0"),
    ({"layer_version": None, "solution_version": "3.0.0"}, "3.0.0"),
    # Falsy but present (empty string, 0) falls back too: the key is read as "no version
    # declared" rather than "declared as empty", by design, not just when the key is absent.
    ({"layer_version": "", "solution_version": "3.0.0"}, "3.0.0"),
    ({"layer_version": 0, "solution_version": "3.0.0"}, "3.0.0"),
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


@pytest.mark.parametrize("rec, due", [
    (record(version="1.1.0", status="halted", target="1.2.0"), True),
    (record(version="1.2.0", status="halted", target="1.1.0"), True),
    (record(version="1.1.0", status="in-progress", target="1.1.0"), False),
    (record(version=None, status="halted", target="1.1.0"), False),
    (record(version="1.1.0+build.7", status="halted", target="1.1.0"), False),
])
def test_report_on_the_resume_of_an_upgrade_or_downgrade(rec, due):
    assert report_due("resume", rec) is due


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


def test_installer_commit_computed_once_with_argv():
    # A shell string run once per record write becomes one argv command in run.yaml.
    run_tasks = _task_file("roles/upgrade_checks/tasks/run.yaml")
    commit_task = next(t for t in run_tasks if t["name"].endswith("Note the installer commit"))
    argv = commit_task["ansible.builtin.command"]["argv"]
    assert argv == ["git", "-C", "{{ role_path }}", "rev-parse", "HEAD"]
    assert commit_task["changed_when"] is False and commit_task["failed_when"] is False

    record_tasks = _task_file("roles/upgrade_checks/tasks/record.yaml")
    write = next(t for t in record_tasks if t["name"].startswith("upgrade_checks | Write the"))
    assert "lookup(" not in write["vars"]["_changes"]["in-progress"]["installedBy"]["installerCommit"]
    assert "_installer_commit" in write["vars"]["_changes"]["in-progress"]["installedBy"]["installerCommit"]


def test_cluster_probe_verdict_is_blocking():
    v = cluster_probe_verdict("erag", "Forbidden")
    assert v["check"] == "cluster-probe" and v["layer"] == "erag"
    assert v["verdict"] == "blocking"
    assert "Forbidden" in v["reason"]
    assert v["resolve"]


def test_cluster_probe_refuses_only_a_layer_that_declares_supports_upgrade():
    probed = [cluster_probe_verdict("erag", "no error text"), cluster_probe_verdict("platform", "no error text")]
    gate = upgrade_gate(probed, {"erag": True, "platform": False})
    assert gate["refused"] == ["erag/cluster-probe"]
    forced_gate = upgrade_gate(probed, {"erag": True, "platform": False}, forced=True)
    assert forced_gate["refused"] == [] and forced_gate["forcedChecks"] == ["erag/cluster-probe"]


def test_cluster_probe_does_not_refuse_with_no_gating_layer_planned():
    probed = [cluster_probe_verdict("platform", "no error text")]
    gate = upgrade_gate(probed, {"platform": False})
    assert gate["refused"] == [] and gate["forced"] is False


def test_cluster_probe_lists_configmaps_not_the_namespace():
    # the probe needs exactly the permission that writing the record needs, and lists
    # rather than gets, so a namespace that does not exist yet still answers.
    tasks = _task_file("roles/upgrade_checks/tasks/run.yaml")
    probe = next(t for t in tasks if t["name"].endswith("Check that the cluster answers"))
    assert probe["kubernetes.core.k8s_info"]["kind"] == "ConfigMap"
    assert "name" not in probe["kubernetes.core.k8s_info"]
    # Scoped to this role's records, not every ConfigMap in the namespace.
    assert probe["kubernetes.core.k8s_info"]["label_selectors"] == \
        ["toolkit-manager.intel.com/record={{ upgrade_record_labels['toolkit-manager.intel.com/record'] }}"]


def test_cluster_probe_keeps_the_error_text():
    # The warning and the cluster-probe verdict quote _record_probe.msg; failed_when: false
    # drops msg on ansible-core 2.19, ignore_errors keeps it.
    tasks = _task_file("roles/upgrade_checks/tasks/run.yaml")
    probe = next(t for t in tasks if t["name"].endswith("Check that the cluster answers"))
    assert probe.get("ignore_errors") is True
    assert "failed_when" not in probe


@pytest.mark.parametrize("release, raw, expected", [
    ("1.2.0", {"version": "1.1.0"}, "1.2.0"),
    (None, {"version": "1.1.0"}, "1.1.0"),
    (None, {}, None),
    (None, None, None),
])
def test_record_version_keeps_the_old_version_when_none_is_resolved(release, raw, expected):
    assert record_version(release, raw) == expected


def test_layer_not_recorded_is_noted_when_a_component_is_skipped():
    # the operator sees why a partial target left the layer unrecorded.
    tasks = _task_file("playbooks/includes/run_layer.yaml")
    note = next(t for t in tasks if t["name"].endswith("is not recorded because a component was skipped"))
    assert "not _layer_fully_planned" in note["when"]


def test_run_layer_rescue_path_stops_the_run_on_a_failing_component(tmp_path):
    # Runs run_layer.yaml for real, with a component that fails, and no cluster (_record_cluster
    # unset keeps _layer_recorded false, so the rescue block's own logic is what is exercised:
    # the halted note, the skipped record write and the stop message).
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    run_layer = os.path.join(repo_root, "playbooks", "includes", "run_layer.yaml")

    component_tasks = tmp_path / "roles" / "a_failing_component" / "tasks"
    component_tasks.mkdir(parents=True)
    (component_tasks / "main.yaml").write_text(
        "- name: Fail on purpose\n"
        "  ansible.builtin.fail:\n"
        "    msg: simulated component failure\n"
    )

    (tmp_path / "ansible.cfg").write_text(textwrap.dedent("""\
        [defaults]
        roles_path = {roles_path}
        filter_plugins = {filter_plugins}
        host_key_checking = False
        """).format(roles_path=tmp_path / "roles", filter_plugins=os.path.join(repo_root, "filter_plugins")))

    playbook = tmp_path / "play.yaml"
    playbook.write_text(textwrap.dedent("""\
        - hosts: localhost
          connection: local
          gather_facts: false
          vars:
            layer: test_layer
            component_action: install
            layers:
              - {{name: test_layer, depends_on: []}}
            components:
              - {{name: a_failing_component, layer: test_layer, depends_on: []}}
            _active_components:
              - {{name: a_failing_component, layer: test_layer}}
            _layer_class: {{}}
            _layer_records: {{}}
          tasks:
            - ansible.builtin.include_tasks: {run_layer}
        """).format(run_layer=run_layer))

    ansible_playbook = os.path.join(os.path.dirname(sys.executable), "ansible-playbook")
    result = subprocess.run(
        [ansible_playbook, str(playbook)],
        cwd=tmp_path, env=dict(os.environ, ANSIBLE_CONFIG=str(tmp_path / "ansible.cfg")),
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert "a_failing_component" in result.stdout
    assert "is reported above" in result.stdout
    # The rescue path noted the run halted, not complete.
    assert "Note the test_layer run as halted" in result.stdout


def test_record_read_failure_gives_the_recovery_hint_not_the_raw_error():
    # A denied or timed-out read must point at the same recovery as a failed cluster probe,
    # not surface the raw Kubernetes error.
    tasks = _task_file("roles/upgrade_checks/tasks/record.yaml")
    read = next(t for t in tasks if t["name"].endswith("Read the {{ upgrade_record_layer }} record"))
    rescue = next(t for t in read["rescue"])
    msg = rescue["ansible.builtin.fail"]["msg"]
    assert "check the cluster connection and retry" in msg
    # any_errors_fatal stops the play here before the gate ever reads _upgrade_force,
    # so the hint must not promise a --force path that does not exist.
    assert "add --force" not in msg


def test_deployment_records_read_failure_gives_the_recovery_hint_not_the_raw_error():
    tasks = _task_file("roles/upgrade_checks/tasks/run.yaml")
    block_task = next(t for t in tasks if t["name"] == "upgrade_checks | Classify and check the install")
    read = next(t for t in block_task["block"] if t["name"].endswith("Read the deployment records"))
    rescue = next(t for t in read["rescue"])
    msg = rescue["ansible.builtin.fail"]["msg"]
    assert "check the cluster connection and retry" in msg
    assert "add --force" not in msg


# upgrade-path check


@pytest.mark.parametrize("recorded, base, verdict", [
    ("3.0.0", ">=3.0.0,<3.1.0", "pass"),
    ("3.0.4", ">=3.0.0,<3.1.0", "pass"),
    ("3.0.0+build.7", ">=3.0.0,<3.1.0", "pass"),
    ("3.0.0", "3.0.0", "pass"),
    ("3.5.0", "3.0.0", "pass"),
    ("2.9.0", "3.0.0", "blocking"),
    ("2.3.0", ">=3.0.0,<3.1.0", "blocking"),
    ("3.1.0", ">=3.0.0,<3.1.0", "blocking"),
    ("3.0.0", "unsupported", "blocking"),
    ("3.0.0", "not a range", "blocking"),
    ("1.2.0", 1.1, "blocking"),
    ("3.0.0", None, "skipped"),
    ("3.0.0", "", "skipped"),
])
def test_upgrade_path_verdict(recorded, base, verdict):
    v = upgrade_path_verdict({"solution_version": "3.1.0", "upgrade_base_version": base},
                             "erag", record(version=recorded), "upgrade", "3.1.0", "dev")
    assert v["verdict"] == verdict
    assert v["check"] == "upgrade-path" and v["layer"] == "erag"
    assert v["reason"]
    assert bool(v["resolve"]) is (verdict == "blocking")


def test_upgrade_path_names_the_range_it_refuses():
    v = upgrade_path_verdict({"upgrade_base_version": ">=3.0.0,<3.1.0"}, "erag", record(version="2.3.0"), "upgrade", None, "dev")
    assert "2.3.0" in v["reason"] and ">=3.0.0,<3.1.0" in v["reason"]
    assert "erag" in v["resolve"] and "dev" in v["resolve"]
    v = upgrade_path_verdict({"upgrade_base_version": "3.x"}, "erag", record(version="2.3.0"), "upgrade", None, "dev")
    assert "'3.x'" in v["reason"] and "not a range" in v["reason"]


def test_upgrade_path_skips_a_layer_without_version_yaml():
    assert upgrade_path_verdict({}, "platform", record(), "upgrade", None, "dev")["verdict"] == "skipped"
    assert upgrade_path_verdict(None, "platform", record(), "upgrade", None, "dev")["verdict"] == "skipped"


def test_upgrade_path_gates_only_a_supports_upgrade_layer():
    v = upgrade_path_verdict({"upgrade_base_version": ">=3.0.0,<3.1.0"}, "erag", record(version="2.3.0"), "upgrade", None)
    assert upgrade_gate([v], {"erag": True})["refused"] == ["erag/upgrade-path"]
    assert upgrade_gate([v], {"erag": False})["refused"] == []
    assert upgrade_gate([v], {"erag": True}, forced=True)["forcedChecks"] == ["erag/upgrade-path"]


def test_upgrade_path_judges_upgrades_downgrades_and_conflicts():
    classes = {"platform": "reinstall", "inference": "fresh", "erag": "upgrade", "x": "downgrade"}
    assert check_layers(classes, "upgrade_path", ["version_change"]) == ["erag", "x"]
    classes["y"] = "conflict"
    assert check_layers(classes, "upgrade_path", ["version_change"], ["conflict"]) == ["erag", "x", "y"]


def test_upgrade_path_skips_a_downgrade():
    data = {"solution_version": "3.0.0", "upgrade_base_version": ">=3.1.0"}
    v = upgrade_path_verdict(data, "erag", record(version="3.1.0"), "downgrade", "3.0.0")
    assert v["verdict"] == "skipped" and "3.1.0 -> 3.0.0" in v["reason"]
    downgrade = version_change_verdict("downgrade", "erag", record(version="3.1.0"), "3.0.0")
    assert upgrade_gate([downgrade, v], {"erag": True}, forced=True)["forcedChecks"] == ["erag/version-change"]


@pytest.mark.parametrize("recorded, release, verdict", [
    ("3.0.0", "3.2.0", "pass"),
    ("2.3.0", "3.2.0", "blocking"),
    ("3.2.0", "3.0.0", "skipped"),
])
def test_upgrade_path_judges_a_conflict_from_the_recorded_version(recorded, release, verdict):
    halted = record(version=recorded, status="halted", target="3.1.0")
    data = {"solution_version": release, "upgrade_base_version": ">=3.0.0"}
    assert classify_run(halted, release, "erag") == "conflict"
    assert upgrade_path_verdict(data, "erag", halted, "conflict", release)["verdict"] == verdict


@pytest.mark.parametrize("rec, reason", [
    (record(version=None, status="halted", target="3.1.0"), "erag has no recorded version to upgrade from"),
    (record(status="deleting"), "a teardown of erag did not finish"),
])
def test_upgrade_path_skips_a_conflict_it_cannot_judge(rec, reason):
    v = upgrade_path_verdict({"solution_version": "3.2.0", "upgrade_base_version": ">=3.0.0"}, "erag", rec, "conflict", "3.2.0")
    assert (v["verdict"], v["reason"]) == ("skipped", reason)


@pytest.mark.parametrize("status", ["halted", "in-progress"])
def test_upgrade_path_blocks_a_conflict_whose_recorded_version_does_not_parse(status):
    # Preflight does not parse an unfinished record's version; this check refuses it for that layer only.
    unfinished = record(version="3.1.0-SNAPSHOT", status=status, target="3.1.0")
    assert classify_run(unfinished, "3.2.0", "erag") == "conflict"
    v = upgrade_path_verdict({"solution_version": "3.2.0", "upgrade_base_version": ">=3.0.0"},
                             "erag", unfinished, "conflict", "3.2.0")
    assert v["verdict"] == "blocking"
    assert v["reason"] == "recorded version: layer erag: '3.1.0-SNAPSHOT' is not a version"
    assert v["resolve"] == ("install erag from a release whose upgrade_base_version covers"
                             " 3.1.0-SNAPSHOT, then this one (--env <env>)")


def test_old_upgrade_reports_are_pruned_like_env_log_dir():
    # Same rule as ENV_LOG_DIR's logs (es_auto_installer.sh): keep the most recent, drop the rest.
    path = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                        "roles", "upgrade_checks", "tasks", "report.yaml")
    with open(path) as f:
        tasks = yaml.safe_load(f)
    listing = next(t for t in tasks if t["name"].endswith("List the {{ upgrade_record_layer }} upgrade reports"))
    assert listing["ansible.builtin.find"]["patterns"] == "{{ upgrade_record_layer }}-upgrade-report-*.yaml"
    prune = next(t for t in tasks if t["name"].endswith("Prune old {{ upgrade_record_layer }} upgrade reports"))
    assert prune["ansible.builtin.file"]["state"] == "absent"
    assert "upgrade_report_keep" in prune["loop"]
