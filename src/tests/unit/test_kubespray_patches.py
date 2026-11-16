# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import pathlib
import re

import yaml

_ROLE = pathlib.Path(__file__).resolve().parents[3] / "roles" / "kubernetes"
_ENSURE = _ROLE / "tasks" / "kubespray_ensure.yaml"
# The Kubespray version the fixtures below come from. When kubespray_version
# moves, check both patches against the new files.
_KUBESPRAY = "v2.31.0"
_CALICO_RESET = """- name: Reset | get and remove remaining routes set by bird
  shell: set -o pipefail && ip route show proto bird | xargs -i bash -c "ip route del {} proto bird "
  args:
    executable: /bin/bash
  changed_when: false
"""


def replace(task, text):
    """What ansible.builtin.replace does with the task's regexp."""
    return re.sub(task["regexp"], task["replace"], text, flags=re.MULTILINE)


def test_the_calico_reset_flushes_the_bird_routes_once_patched():
    assert yaml.safe_load((_ROLE / "defaults" / "main.yaml").read_text())["kubespray_version"] == _KUBESPRAY
    task = next(t for t in yaml.safe_load(_ENSURE.read_text()) if "ansible.builtin.replace" in t)
    task = task["ansible.builtin.replace"]
    assert task["path"].endswith("/roles/network_plugin/calico/tasks/reset.yml")
    patched = replace(task, _CALICO_RESET)
    assert "  shell: set -o pipefail && ip route flush proto bird\n" in patched
    assert replace(task, patched) == patched


# roles/kubernetes/node/templates/kubelet-config.v1beta1.yaml.j2, lines 128-130
_KUBELET_ANCHOR = """{% if kubelet_topology_manager_policy is defined %}
topologyManagerPolicy: {{ kubelet_topology_manager_policy }}
{% endif %}
"""


def test_the_kubelet_template_is_patched_on_every_action():
    # Every action resets the clone first, and add-node's scale.yml renders the new node's kubelet config.
    blocks = [t["ansible.builtin.blockinfile"] for t in yaml.safe_load(_ENSURE.read_text())
              if "ansible.builtin.blockinfile" in t]
    assert [b["path"] for b in blocks] == ["{{ kubespray_dir }}/roles/kubernetes/node/templates/kubelet-config.v1beta1.yaml.j2"]
    assert len(re.findall(blocks[0]["insertafter"], _KUBELET_ANCHOR)) == 1
    assert "reservedSystemCPUs: {{ kubelet_reserved_cpus }}" in blocks[0]["block"]
