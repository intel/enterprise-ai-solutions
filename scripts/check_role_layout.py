#!/usr/bin/env python3
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Checks the layout every component role follows (docs/reference/adding_solutions.md):

  roles/<role>/
  ├── defaults/main.yaml        public settings only; no platform logic
  ├── vars/                     optional, both files or neither
  │   ├── kubernetes.yaml       private _<role>_* literal values
  │   └── openshift.yaml
  ├── templates/                no cluster_distribution
  └── tasks/
      ├── main.yaml             the canonical dispatcher below
      ├── install.yaml          required
      ├── validate.yaml         required
      ├── teardown.yaml         required
      ├── install_openshift.yaml    optional; its presence opts the role in,
      ├── validate_openshift.yaml   and then all three are required
      ├── teardown_openshift.yaml
      └── <helpers>.yaml        called from the files above, platform-neutral

  scripts/check_role_layout.py          report violations, exit 1 if any
  scripts/check_role_layout.py --fix    also rewrite every tasks/main.yaml
"""

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
ROLES = ROOT / "roles"

ACTIONS = ("install", "validate", "teardown")
PLATFORMS = ("kubernetes", "openshift")

# Roles with a main.yaml that are not components.
NOT_COMPONENTS = {
    "backup": "backup/restore/validate actions, run by its own playbook",
    "ownership": "helper role, included by playbooks/includes/run_component.yaml",
}

# Library roles: no main.yaml, entered with tasks_from. Their callers pass
# vars_from: "{{ cluster_distribution }}.yaml" when they have platform vars
# (roles/gateway for the envoy_gateway provider hooks).
LIBRARIES = {"envoy_gateway", "mesh"}

# Files that may still read cluster_distribution, and why.
PLATFORM_ALLOWED = {
    # Runs in preflight (tasks_from), before any dispatcher: maps the storage
    # selector to the platform. Configuration resolution, not role behaviour.
    "roles/storage/tasks/resolve.yaml",
}

DISPATCHER = """\
# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
---
# Canonical dispatcher, the same in every component role but for the name.
# Written by scripts/check_role_layout.py --fix; change it there, not here.
#   vars/<cluster_distribution>.yaml   values that differ by platform, if any
#   tasks/install_openshift.yaml       opts the role into its own OpenShift
#                                      install, validate and teardown files
- name: "{role} | dispatch | Assert supported action"
  ansible.builtin.assert:
    that: component_action | default('') in ['install', 'teardown', 'validate']
    fail_msg: "{role}: unsupported component_action='{{{{ component_action | default('') }}}}'"
    quiet: true

- name: "{role} | dispatch | Load {{{{ cluster_distribution }}}} values"
  ansible.builtin.include_vars: "{{{{ role_path }}}}/vars/{{{{ cluster_distribution }}}}.yaml"
  when: (role_path ~ '/vars/' ~ cluster_distribution ~ '.yaml') is file

- name: "{role} | dispatch | Include {{{{ component_action }}}}"
  vars:
    _variant: >-
      {{{{ '_openshift' if cluster_distribution == 'openshift'
         and (role_path ~ '/tasks/install_openshift.yaml') is file else '' }}}}
  ansible.builtin.include_tasks: "{{{{ component_action }}}}{{{{ _variant }}}}.yaml"
"""

# Not in comments: a Jinja or YAML line that reads the variable. A library
# role's caller loads its platform vars with exactly this line.
PLATFORM_RE = re.compile(r"^(?!\s*#)(?!\s*vars_from: \"\{\{ cluster_distribution \}\}\.yaml\"$).*\bcluster_distribution\b", re.M)


def rel(path):
    return str(path.relative_to(ROOT))


def check_vars(role, errors):
    present = [p for p in PLATFORMS if (role / "vars" / f"{p}.yaml").is_file()]
    if not present:
        return
    if len(present) != len(PLATFORMS):
        errors.append(f"{rel(role / 'vars')}: has {present[0]}.yaml only; both platforms or neither")
        return
    prefix = f"_{role.name}_"
    keys = {}
    for p in PLATFORMS:
        path = role / "vars" / f"{p}.yaml"
        data = yaml.safe_load(path.read_text()) or {}
        keys[p] = set(data)
        for key in sorted(k for k in data if not k.startswith(prefix)):
            errors.append(f"{rel(path)}: {key} is not private ({prefix}*)")
        for key, val in data.items():
            if isinstance(val, str) and "{{" in val:
                errors.append(f"{rel(path)}: {key} is an expression; platform vars are literal values")
    for a, b in (PLATFORMS, PLATFORMS[::-1]):
        for key in sorted(keys[a] - keys[b]):
            errors.append(f"{rel(role / 'vars' / (b + '.yaml'))}: missing {key} (set in {a}.yaml)")


def check_component(role, fix, errors):
    tasks = role / "tasks"
    main = tasks / "main.yaml"
    want = DISPATCHER.format(role=role.name)
    if main.read_text() != want:
        if fix:
            main.write_text(want)
        else:
            errors.append(f"{rel(main)}: not the canonical dispatcher (--fix rewrites it)")
    for action in ACTIONS:
        if not (tasks / f"{action}.yaml").is_file():
            errors.append(f"{rel(tasks)}: missing {action}.yaml")
    variants = [a for a in ACTIONS if (tasks / f"{a}_openshift.yaml").is_file()]
    if variants and "install" not in variants:
        errors.append(f"{rel(tasks)}: {variants[0]}_openshift.yaml without install_openshift.yaml is never run")
    elif variants:
        for action in ACTIONS:
            if action not in variants:
                errors.append(f"{rel(tasks)}: install_openshift.yaml needs {action}_openshift.yaml too")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix", action="store_true", help="rewrite tasks/main.yaml to the canonical dispatcher")
    args = parser.parse_args()

    errors = []
    for role in sorted(p for p in ROLES.iterdir() if p.is_dir()):
        if role.name in NOT_COMPONENTS:
            continue
        if role.name in LIBRARIES:
            if (role / "tasks" / "main.yaml").exists():
                errors.append(f"{rel(role)}: library role with a tasks/main.yaml")
        elif (role / "tasks" / "main.yaml").is_file():
            check_component(role, args.fix, errors)
        else:
            errors.append(f"{rel(role)}: no tasks/main.yaml; add it, or list the role in LIBRARIES")
        check_vars(role, errors)
        for sub in ("defaults", "tasks", "templates", "vars"):
            for path in sorted((role / sub).rglob("*")):
                if not path.is_file() or rel(path) in PLATFORM_ALLOWED:
                    continue
                text = path.read_text(errors="replace")
                if path == role / "tasks" / "main.yaml" and text == DISPATCHER.format(role=role.name):
                    continue    # the one place that reads it
                for m in PLATFORM_RE.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    errors.append(f"{rel(path)}:{line}: reads cluster_distribution; use vars/<platform>.yaml "
                                  "or an _openshift task file")

    for err in errors:
        print(err)
    if errors:
        print(f"\n{len(errors)} problem(s); see docs/reference/adding_solutions.md", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
