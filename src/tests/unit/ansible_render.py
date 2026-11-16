# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Render a template string as Ansible does, for the unit tests."""
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

try:
    from ansible.template import trust_as_template
except ImportError:  # ansible-core < 2.19 (requirements.txt allows 2.17) templates plain strings
    def trust_as_template(source):
        return source


def render(source, variables):
    return Templar(loader=DataLoader(), variables=variables).template(trust_as_template(source))
