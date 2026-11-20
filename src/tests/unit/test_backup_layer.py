# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "filter_plugins"))

from backup_layer import (  # noqa: E402
    backup_profile_path,
    checkout_matches_record,
    restore_backup_choice,
    restore_layer_selector,
)

LAYERS = ["infrastructure", "platform", "inference", "erag"]
LAYER_DIRS = {
    "inference": "/repo/ext/enterprise.ai-inference",
    "erag": "/repo/ext/enterprise.ai-erag/deployment",
}


def test_profile_sits_beside_the_layers_components_yaml():
    assert backup_profile_path("erag", LAYER_DIRS) == "/repo/ext/enterprise.ai-erag/deployment/backup_profile.yaml"
    assert backup_profile_path("inference", LAYER_DIRS) == "/repo/ext/enterprise.ai-inference/backup_profile.yaml"


@pytest.mark.parametrize("layer", ["platform", "infrastructure", "nosuch"])
def test_layer_without_its_own_repository_has_no_profile_path(layer):
    assert backup_profile_path(layer, LAYER_DIRS) == ""


def test_no_ext_directory_means_no_profile_path():
    assert backup_profile_path("erag", None) == ""


def test_restore_leaves_every_other_layer_out():
    selector = restore_layer_selector("erag", LAYERS, "toolkit-manager.intel.com/layer")
    assert selector == {"matchExpressions": [{
        "key": "toolkit-manager.intel.com/layer",
        "operator": "NotIn",
        "values": ["inference", "infrastructure", "platform"],
    }]}


def test_restore_of_the_only_layer_needs_no_selector():
    assert restore_layer_selector("erag", ["erag"], "k") is None
    assert restore_layer_selector("erag", [], "k") is None


@pytest.mark.parametrize("recorded, checkout, matches", [
    ("3.0.0", "3.0.0", True),
    ("3.0", "3.0.0", True),
    ("3.0.0", "3.1.0", False),
    ("3.0.0", "", False),
    ("3.0.0", None, False),
    ("", "3.1.0", True),
    (None, None, True),
    ("main", "main", True),
    ("main", "dev", False),
])
def test_checkout_matches_record(recorded, checkout, matches):
    assert checkout_matches_record(recorded, checkout) is matches


VERSION_LABEL = "erag.example/version"


def _backup(name, completed, version="3.0.0", phase="Completed"):
    return {"metadata": {"name": name, "labels": {VERSION_LABEL: version}},
            "status": {"phase": phase, "completionTimestamp": completed}}


def test_restore_backup_choice_picks_the_newest_backup_of_the_recorded_version():
    candidates = [
        _backup("older", "2026-10-28T07:00:00Z"),
        _backup("newer", "2026-10-28T11:00:00Z"),
        _backup("other-version", "2026-10-28T12:00:00Z", version="2.9.0"),
    ]
    assert restore_backup_choice(candidates, VERSION_LABEL, "3.0.0") == {"name": "newer"}


def test_restore_backup_choice_compares_versions_not_strings():
    candidates = [_backup("b", "2026-10-28T11:00:00Z", version="3.0")]
    assert restore_backup_choice(candidates, VERSION_LABEL, "3.0.0") == {"name": "b"}


def test_restore_backup_choice_refuses_without_a_matching_backup():
    candidates = [_backup("other-version", "2026-10-28T11:00:00Z", version="2.9.0"),
                  _backup("not-completed", "2026-10-28T12:00:00Z", version="3.0.0", phase="InProgress")]
    result = restore_backup_choice(candidates, VERSION_LABEL, "3.0.0")
    assert result["error"] == "no match"
    assert result["available"] == ["other-version (2.9.0)"]


def test_restore_backup_choice_refuses_without_a_record():
    candidates = [_backup("b", "2026-10-28T11:00:00Z")]
    assert restore_backup_choice(candidates, VERSION_LABEL, "") == {"error": "no record", "available": []}


def test_restore_backup_choice_override_wins_outright():
    candidates = [_backup("other-version", "2026-10-28T11:00:00Z", version="2.9.0")]
    assert restore_backup_choice(candidates, VERSION_LABEL, "", "pinned") == {"name": "pinned"}
    assert restore_backup_choice(candidates, VERSION_LABEL, "3.0.0", "pinned") == {"name": "pinned"}


BYPASS_LABEL = "toolkit-manager.intel.com/checkout-bypass"


def _forced(name, completed, version="3.0.0"):
    backup = _backup(name, completed, version)
    backup["metadata"]["labels"][BYPASS_LABEL] = "checkout-version"
    return backup


def test_restore_backup_choice_skips_a_newer_forced_backup():
    candidates = [_backup("regular", "2026-10-28T07:00:00Z"), _forced("forced", "2026-10-28T11:00:00Z")]
    assert restore_backup_choice(candidates, VERSION_LABEL, "3.0.0", "", BYPASS_LABEL) == {"name": "regular"}


def test_restore_backup_choice_refuses_with_only_forced_backups_and_names_them():
    candidates = [_forced("forced-a", "2026-10-28T07:00:00Z"), _forced("forced-b", "2026-10-28T11:00:00Z"),
                  _backup("other-version", "2026-10-28T12:00:00Z", version="2.9.0")]
    result = restore_backup_choice(candidates, VERSION_LABEL, "3.0.0", "", BYPASS_LABEL)
    assert result["error"] == "no match"
    assert result["forced"] == ["forced-a", "forced-b"]
    assert "other-version (2.9.0)" in result["available"]


def test_restore_backup_choice_no_match_without_forced_has_no_forced_key():
    candidates = [_backup("other-version", "2026-10-28T11:00:00Z", version="2.9.0")]
    assert "forced" not in restore_backup_choice(candidates, VERSION_LABEL, "3.0.0", "", BYPASS_LABEL)


def test_restore_backup_choice_from_still_restores_a_forced_backup():
    candidates = [_forced("forced", "2026-10-28T11:00:00Z")]
    assert restore_backup_choice(candidates, VERSION_LABEL, "3.0.0", "forced", BYPASS_LABEL) == {"name": "forced"}


def _task_file(path):
    return yaml.safe_load((Path(__file__).resolve().parents[3] / path).read_text())


def _run_play(tmp_path, play):
    (tmp_path / "play.yaml").write_text(yaml.safe_dump([play]))
    ansible_playbook = Path(sys.executable).parent / "ansible-playbook"
    filters = Path(__file__).resolve().parents[3] / "filter_plugins"
    return subprocess.run([str(ansible_playbook), str(tmp_path / "play.yaml")], cwd=tmp_path,
                          env=dict(os.environ, ANSIBLE_FILTER_PLUGINS=str(filters)),
                          capture_output=True, text=True, timeout=60)


def test_velero_server_error_does_not_read_the_failed_flag():
    # failed_when: false clears it, so an API error must be read from the missing resources key.
    query, note = _task_file("roles/backup/tasks/velero_server.yaml")
    assert query["failed_when"] is False
    error = note["ansible.builtin.set_fact"]["_bk_velero_server_error"]
    assert "is failed" not in error and "'resources' in _bk_velero_server_pods" in error


@pytest.mark.parametrize("version_yaml, force, outcome", [
    (None, False, "taken"),                       # no version_file: nothing to compare
    ("solution_version: 3.0\n", False, "taken"),   # 3.0 is 3.0.0
    ("solution_version: 3.1.0\n", False, "refused"),
    ("solution_version: 3.1.0\n", True, "forced"),
])
def test_backup_checks_the_version_it_labels_the_backup_with(tmp_path, version_yaml, force, outcome):
    tasks = _task_file("roles/backup/tasks/layer.yaml")
    guard = [t for t in tasks if t["name"].startswith(("backup | Refuse to back up", "backup | Label a"))]
    profile = {"name": "p", "version_label": "", "version_file": ""}
    if version_yaml:
        (tmp_path / "version.yaml").write_text(version_yaml)
        profile.update(version_label="l", version_file=str(tmp_path / "version.yaml"))
    resolve = Path(__file__).resolve().parents[3] / "roles" / "backup" / "tasks" / "resolve_version.yaml"
    result = _run_play(tmp_path, {
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"backup_profile": profile, "backup_layer": "erag", "component_action": "backup",
                 "_bk_recorded_version": "3.0.0", "backup_force": force, "backup_labels": {},
                 "backup_checkout_bypass_label_key": BYPASS_LABEL, "backup_manage_command_prefix": "run %s"},
        "tasks": [{"ansible.builtin.include_tasks": str(resolve)}] + guard +
                 [{"ansible.builtin.debug": {"msg": "LABELS={{ backup_labels | to_json }}"}}]})
    if outcome == "refused":
        assert result.returncode != 0 and "this run would label the backup" in result.stdout, result.stdout
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert (BYPASS_LABEL in result.stdout.split("LABELS=")[1]) is (outcome == "forced")


@pytest.mark.parametrize("version_yaml, version_key, expected", [
    ("layer_version: 1.2.0\nsolution_version: 3.1.0\n", "", "1.2.0"),
    ("solution_version: 3.1.0\n", "", "3.1.0"),
    ("layer_version: 1.2.0\nsolution_version: 3.1.0\n", "solution_version", "3.1.0"),
])
def test_the_backup_version_is_read_as_the_deployment_record_reads_it(tmp_path, version_yaml, version_key, expected):
    # Without version_key the label must equal the record's version (layer_version, else
    # solution_version), or backup-freshness never matches the backup.
    (tmp_path / "version.yaml").write_text(version_yaml)
    resolve = Path(__file__).resolve().parents[3] / "roles" / "backup" / "tasks" / "resolve_version.yaml"
    profile = {"name": "p", "version_label": "l", "version_file": str(tmp_path / "version.yaml"),
               "version_key": version_key}
    result = _run_play(tmp_path, {
        "hosts": "localhost", "connection": "local", "gather_facts": False,
        "vars": {"backup_profile": profile},
        "tasks": [{"ansible.builtin.include_tasks": str(resolve)},
                  {"ansible.builtin.debug": {"msg": "VERSION={{ _bk_version }}"}}]})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "VERSION=%s" % expected in result.stdout
