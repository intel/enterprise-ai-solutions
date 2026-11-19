# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""run_backup.sh: the ansible-playbook arguments it assembles, against a fixture env.

The script and the installer it sources are copied into a scratch tree whose
.venv/bin/ansible-playbook is a stub that records its arguments.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(shutil.which("yq") is None, reason="needs yq")


@pytest.fixture
def tree(tmp_path):
    for name in ("run_backup.sh", "es_auto_installer.sh", "VERSION"):
        if (REPO / name).exists():
            shutil.copy2(REPO / name, tmp_path / name)
    shutil.copytree(REPO / "configs", tmp_path / "configs")
    (tmp_path / "playbooks").mkdir()
    shutil.copy2(REPO / "playbooks" / "backup.yaml", tmp_path / "playbooks" / "backup.yaml")

    stub_bin = tmp_path / "stub-bin"
    venv_bin = tmp_path / ".venv" / "bin"
    stub_bin.mkdir()
    venv_bin.mkdir(parents=True)
    for tool in ("kubectl", "helm"):
        (stub_bin / tool).write_text("#!/bin/sh\nexit 0\n")
        (stub_bin / tool).chmod(0o755)
    (venv_bin / "ansible-playbook").write_text(
        '#!/bin/sh\nfor a in "$@"; do printf \'%s\\n\' "$a"; done > "$ARGS_OUT"\n'
    )
    (venv_bin / "ansible-playbook").chmod(0o755)

    env = tmp_path / "env" / "t"
    (env / "inventory").mkdir(parents=True)
    (env / "inventory" / "hosts.yaml").write_text("all: {}\n")
    (env / "global_config.yaml").write_text("{}\n")
    (env / "config.inference.yaml").write_text("{}\n")
    (env / "config.erag.yaml").write_text("{}\n")
    (env / ".solutions.yaml").write_text(
        "schema: 2\ninited: []\nlayers:\n"
        "  - {name: inference, config: config.inference.yaml}\n"
        "  - {name: erag, config: config.erag.yaml}\n"
    )
    return tmp_path


def run(tree, *args):
    out = tree / "args.txt"
    out.unlink(missing_ok=True)
    env = dict(os.environ, PATH=f"{tree / 'stub-bin'}:{os.environ['PATH']}", ARGS_OUT=str(out))
    env.pop("ES_LOG_LEVEL", None)
    proc = subprocess.run(
        ["bash", str(tree / "run_backup.sh"), *args],
        cwd=tree, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False,
    )
    argv = out.read_text().splitlines() if out.exists() else None
    return proc, argv


def extra_vars(argv):
    return [argv[i + 1] for i, a in enumerate(argv) if a == "-e"]


def test_backup_passes_env_configs_in_record_order(tree):
    proc, argv = run(tree, "backup", "platform", "--env", "t")
    assert proc.returncode == 0, proc.stderr
    env = tree / "env" / "t"
    ev = extra_vars(argv)
    assert argv[argv.index("-i") + 1] == str(env / "inventory" / "hosts.yaml")
    assert str(tree / "playbooks" / "backup.yaml") in argv
    cfgs = [v for v in ev if v.startswith("@")]
    assert cfgs == [f"@{env}/config.inference.yaml", f"@{env}/config.erag.yaml", f"@{env}/global_config.yaml"]
    assert f"env_dir={env}" in ev
    assert f"kubernetes_kubeconfig={env}/kubeconfig.yaml" in ev
    assert "target=platform" in ev and "backup_action=backup" in ev
    assert not any(v.startswith(("backup_confirm=", "velero_restore_from=", "backup_force=")) for v in ev)
    assert list((env / "logs").glob("backup-platform-*.log"))


def test_backup_force_takes_it_from_another_checkout(tree):
    proc, argv = run(tree, "backup", "platform", "--env", "t", "--force")
    assert proc.returncode == 0, proc.stderr
    ev = extra_vars(argv)
    assert "backup_force=true" in ev and not any(v.startswith("backup_confirm=") for v in ev)


def test_restore_with_force_and_from(tree):
    proc, argv = run(tree, "restore", "platform", "--env", "t", "--from", "bk-1", "--force", "--", "-vv")
    assert proc.returncode == 0, proc.stderr
    ev = extra_vars(argv)
    assert "backup_action=restore" in ev and "backup_confirm=yes" in ev
    assert "velero_restore_from=bk-1" in ev and "backup_force=true" not in ev
    assert argv[-1] == "-vv"


def test_restore_without_terminal_or_force_refuses(tree):
    proc, argv = run(tree, "restore", "platform", "--env", "t")
    assert proc.returncode != 0 and argv is None
    assert "Refusing: stdin is not a terminal" in proc.stderr


def test_list_and_unknown_layer(tree):
    proc, argv = run(tree, "list", "platform", "--env", "t")
    assert proc.returncode == 0, proc.stderr
    assert "backup_action=list" in extra_vars(argv)
    proc, argv = run(tree, "list", "nosuchlayer", "--env", "t")
    assert proc.returncode != 0 and argv is None and "Unknown layer" in proc.stderr


def test_installer_hints_name_the_installer(tree):
    (tree / "env" / "t" / ".solutions.yaml").write_text("schema: 1\nlayers: []\n")
    proc, argv = run(tree, "backup", "platform", "--env", "t")
    assert proc.returncode != 0 and argv is None
    assert "./es_auto_installer.sh init" in proc.stderr
