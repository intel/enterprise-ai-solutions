# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Render a template string as Ansible does, for the unit tests."""
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar, trust_as_template


def render(source, variables):
    return Templar(loader=DataLoader(), variables=variables).template(trust_as_template(source))
