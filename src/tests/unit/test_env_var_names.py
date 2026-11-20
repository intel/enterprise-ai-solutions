# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# TKM_LOG_LEVEL / TKM_ENV are the current names; ES_LOG_LEVEL / ES_ENV must keep
# working, and the new name wins when both are set.

import os
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]


def installer(**env):
    base = {k: v for k, v in os.environ.items() if k not in ("TKM_LOG_LEVEL", "ES_LOG_LEVEL")}
    return subprocess.run([str(ROOT / "toolkit_manager.sh"), "--version"], capture_output=True,
                          text=True, env={**base, **env}, check=False)


@pytest.mark.parametrize("name", ["TKM_LOG_LEVEL", "ES_LOG_LEVEL"])
def test_either_log_level_name_is_read(name):
    bad = installer(**{name: "bogus"})
    assert bad.returncode == 1 and "must be info, debug, or trace" in bad.stderr
    traced = installer(**{name: "trace"})
    assert traced.returncode == 0 and "+ " in traced.stderr


def test_new_log_level_name_wins():
    assert installer(TKM_LOG_LEVEL="info", ES_LOG_LEVEL="bogus").returncode == 0
    assert installer(TKM_LOG_LEVEL="bogus", ES_LOG_LEVEL="info").returncode == 1


@pytest.fixture
def repo(tmp_path):
    """A repo root with model-manager, two envs and a stub that prints what it got."""
    shutil.copy(ROOT / "model-manager", tmp_path / "model-manager")
    stub = tmp_path / "ext/enterprise.ai-inference/model_manager/model-manager"
    stub.parent.mkdir(parents=True)
    stub.write_text('#!/bin/bash\necho "$TKM_ENV $ES_ENV $MM_GLOBAL_CONFIG"\n')
    stub.chmod(0o755)
    for e in ("one", "two"):
        (tmp_path / "env" / e).mkdir(parents=True)
        (tmp_path / "env" / e / "global_config.yaml").write_text("{}\n")
        (tmp_path / "env" / e / "config.inference.yaml").write_text("{}\n")
    return tmp_path


def model_manager(root, **env):
    base = {k: v for k, v in os.environ.items() if k not in ("TKM_ENV", "ES_ENV")}
    return subprocess.run([str(root / "model-manager"), "list"], capture_output=True, text=True,
                          env={**base, **env}, stdin=subprocess.DEVNULL, check=False)


@pytest.mark.parametrize("name", ["TKM_ENV", "ES_ENV"])
def test_either_env_name_selects_the_env_and_both_are_exported(repo, name):
    out = model_manager(repo, **{name: "two"})
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["two", "two", str(repo / "env/two/global_config.yaml")]


def test_new_env_name_wins(repo):
    assert model_manager(repo, TKM_ENV="one", ES_ENV="two").stdout.split()[0] == "one"


def test_unknown_env_is_refused(repo):
    out = model_manager(repo, ES_ENV="three")
    assert out.returncode == 1 and "env/three/" in out.stderr
