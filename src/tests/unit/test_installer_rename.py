# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# es_auto_installer.sh is the former name of toolkit_manager.sh: it must behave
# the same when run (exit code, output, any working directory) and when sourced.

import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[3]
OLD = ROOT / "es_auto_installer.sh"
NEW = ROOT / "toolkit_manager.sh"
NOTICE = "es_auto_installer.sh is now toolkit_manager.sh"


def run(script, *args, cwd=None):
    return subprocess.run([str(script), *args], capture_output=True, text=True, cwd=cwd, check=False)


def test_old_name_runs_the_installer_with_a_notice_on_stderr_only():
    old, new = run(OLD, "--version"), run(NEW, "--version")
    assert old.returncode == new.returncode == 0
    assert old.stdout == new.stdout and new.stdout.startswith("toolkit_manager ")
    assert NOTICE in old.stderr and NOTICE not in new.stderr


def test_old_name_keeps_the_exit_code_and_arguments():
    old = run(OLD, "install", "nosuchtarget", "--env", "nosuchenv")
    new = run(NEW, "install", "nosuchtarget", "--env", "nosuchenv")
    assert old.returncode == new.returncode != 0
    assert old.stdout == new.stdout


def test_old_name_works_from_any_directory(tmp_path):
    assert run(OLD, "--version", cwd=tmp_path).stdout == run(NEW, "--version").stdout


def test_sourcing_the_old_name_loads_the_installer_without_running_it():
    out = subprocess.run(["bash", "-c", f'source "{OLD}"; echo "$SCRIPT_DIR"; type -t main'],
                         capture_output=True, text=True, check=True)
    assert out.stdout.split() == [str(ROOT), "function"]
    assert NOTICE not in out.stderr


def test_both_names_are_executable():
    assert OLD.stat().st_mode & 0o111 and NEW.stat().st_mode & 0o111
