# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import os
import pathlib
import shutil
import subprocess

import pytest

_WRAPPER = pathlib.Path(__file__).resolve().parents[3] / "model-manager"
needs_yq = pytest.mark.skipif(shutil.which("yq") is None, reason="the wrapper reads global_config.yaml with yq")


def flags(vendor):
    return f"rack_scale_gpu_vendor={vendor}\nrack_scale_gpu_model=b300\n"


NVIDIA = flags("nvidia")


def accel_resource(tmp_path, global_config, flags_env, env=None, path=None):
    """MM_ACCEL_RESOURCE as ./model-manager hands it to the inference model-manager, for env e1."""
    repo = tmp_path / "repo"
    real = repo / "ext" / "enterprise.ai-inference" / "model_manager" / "model-manager"
    real.parent.mkdir(parents=True)
    real.write_text('#!/bin/sh\nprintf %s "${MM_ACCEL_RESOURCE-<unset>}"\n')
    real.chmod(0o755)
    shutil.copy(_WRAPPER, repo / "model-manager")
    metadata = repo / "env" / "e1" / "inventory" / "metadata"
    metadata.mkdir(parents=True)
    if global_config is not None:
        (repo / "env" / "e1" / "global_config.yaml").write_text(global_config)
    if flags_env is not None:
        (metadata / "rack-scale-flags.env").write_text(flags_env)
    run_env = {"PATH": path or os.environ["PATH"], "HOME": str(tmp_path), "ES_ENV": "e1", **(env or {})}
    return subprocess.run(["bash", str(repo / "model-manager"), "list"], env=run_env,
                          capture_output=True, text=True, check=True).stdout


@needs_yq
@pytest.mark.parametrize("global_config, flags_env, expected", [
    ("rack_scale_enabled: true\n", NVIDIA, "nvidia.com/gpu"),
    ("rack_scale_enabled: True\n", NVIDIA, "nvidia.com/gpu"),
    ("rack_scale_enabled: yes\n", NVIDIA, "nvidia.com/gpu"),
    ("rack_scale_enabled: on\n", NVIDIA, "nvidia.com/gpu"),
    ("rack_scale_enabled: 1\n", NVIDIA, "nvidia.com/gpu"),
    ("rack_scale_enabled: TRUE\n", NVIDIA, "nvidia.com/gpu"),
    ("rack_scale_enabled: y\n", NVIDIA, "<unset>"),  # Ansible's bool filter reads y and t as false
    ("rack_scale_enabled: t\n", NVIDIA, "<unset>"),
    ("rack_scale_enabled: false\n", NVIDIA, "<unset>"),  # flags left from an earlier rack-scale install
    ("rack_scale_enabled: true\n", flags(""), "<unset>"),  # no accelerator in the rack profile
    ("rack_scale_enabled: true\n", flags("intel"), "<unset>"),  # no GPU runtime for this vendor yet
    ("rack_scale_enabled: true\n", None, "<unset>"),
    ("storage_backend: nfs\n", None, "<unset>"),  # a standard install
    ("storage_backend: nfs\n", NVIDIA, "<unset>"),  # a standard install with flags left from rack-scale
])
def test_only_a_rack_scale_nvidia_env_sets_the_gpu_resource(tmp_path, global_config, flags_env, expected):
    assert accel_resource(tmp_path, global_config, flags_env) == expected


@needs_yq
@pytest.mark.parametrize("exported, expected", [("nvidia.com/mig-1g.10gb", "nvidia.com/mig-1g.10gb"),
                                                ("", "nvidia.com/gpu")])
def test_an_exported_value_wins_and_empty_counts_as_unset(tmp_path, exported, expected):
    env = {"MM_ACCEL_RESOURCE": exported}
    assert accel_resource(tmp_path, "rack_scale_enabled: true\n", NVIDIA, env) == expected


@needs_yq
def test_the_vendor_ends_at_whitespace_like_the_installers_regex(tmp_path):
    assert accel_resource(tmp_path, "rack_scale_enabled: true\n", flags("nvidia\r")) == "nvidia.com/gpu"


def test_without_yq_nothing_is_exported(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "readlink", "dirname"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    assert accel_resource(tmp_path, "rack_scale_enabled: true\n", NVIDIA, path=str(bin_dir)) == "<unset>"


@needs_yq
def test_without_global_config_nothing_is_exported(tmp_path):
    other_env = tmp_path / "other.yaml"
    other_env.write_text("rack_scale_enabled: true\n")
    assert accel_resource(tmp_path, None, NVIDIA, {"MM_GLOBAL_CONFIG": str(other_env)}) == "<unset>"


@needs_yq
def test_the_first_vendor_line_counts_like_the_installers_regex(tmp_path):
    flags_env = "rack_scale_gpu_vendor=nvidia\nrack_scale_gpu_vendor=intel\n"
    assert accel_resource(tmp_path, "rack_scale_enabled: true\n", flags_env) == "nvidia.com/gpu"
