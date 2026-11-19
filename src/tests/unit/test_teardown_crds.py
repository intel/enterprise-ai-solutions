# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
"""helm leaves CRDs behind on uninstall, so two teardowns delete their own.

Deleting a CRD takes every custom resource in the group with it, so what these
tasks select, and when they are skipped, is what the tests below pin down.
"""
import pathlib

import yaml
from ansible_render import render

_ROLES = pathlib.Path(__file__).resolve().parents[3] / "roles"
_CNPG = yaml.safe_load((_ROLES / "postgresql" / "tasks" / "teardown.yaml").read_text())
_NRI = yaml.safe_load((_ROLES / "nri_cpu_balloons" / "tasks" / "teardown.yaml").read_text())

OURS = {"postgresql_namespace": "postgresql", "postgresql_cluster_name": "postgresql"}


def flat(tasks):
    """Tasks with the contents of every block spliced in, parents first."""
    for t in tasks:
        yield t
        if "block" in t:
            yield from flat(t["block"])


def task(tasks, name):
    return next(t for t in flat(tasks) if t.get("name", "").endswith(name))


def parent_of(tasks, name):
    return next(t for t in tasks if "block" in t and any(
        b.get("name", "").endswith(name) for b in flat(t["block"])))


def listed(value):
    """A rendered list: ansible-core < 2.19 renders it as text here."""
    return value if isinstance(value, list) else yaml.safe_load(value)


def cond(expression, variables):
    """A `when:` is bare Jinja, so it needs the braces a template has."""
    conditions = expression if isinstance(expression, list) else [expression]
    rendered = [render("{{ %s }}" % c, variables) for c in conditions]
    return all(r if isinstance(r, bool) else yaml.safe_load(str(r)) for r in rendered)


def crd(name, group):
    return {"metadata": {"name": name}, "spec": {"group": group}}


def cluster(namespace, name):
    return {"metadata": {"namespace": namespace, "name": name}}


def foreign(*clusters):
    expression = task(_CNPG, "Note the CNPG Clusters that are not ours")["ansible.builtin.set_fact"]["_cnpg_foreign"]
    variables = dict(OURS, _cnpg_clusters={"resources": list(clusters)})
    return listed(render(expression, variables))


def test_only_the_cnpg_group_is_deleted():
    crds = [crd("clusters.postgresql.cnpg.io", "postgresql.cnpg.io"),
            crd("databases.postgresql.cnpg.io", "postgresql.cnpg.io"),
            crd("clusters.example.postgresql.cnpg.io.test", "example.postgresql.cnpg.io.test"),
            crd("certificates.cert-manager.io", "cert-manager.io"),
            crd("balloonspolicies.config.nri", "config.nri")]
    selected = listed(render(task(_CNPG, "Delete the CNPG CRDs")["loop"], {"_cnpg_crds": {"resources": crds}}))
    assert [c["metadata"]["name"] for c in selected] == ["clusters.postgresql.cnpg.io",
                                                         "databases.postgresql.cnpg.io"]


def test_a_cluster_of_someone_else_is_foreign():
    assert foreign(cluster("postgresql", "postgresql")) == []
    assert foreign() == []
    assert foreign(cluster("tenant-a", "orders")) == ["tenant-a/orders"]
    assert foreign(cluster("postgresql", "scratch")) == ["postgresql/scratch"]
    assert foreign(cluster("postgresql", "postgresql"),
                   cluster("tenant-a", "orders")) == ["tenant-a/orders"]


def gate(variables):
    return cond(task(_CNPG, "Remove the CNPG CRDs")["when"], variables)


def test_a_foreign_cluster_keeps_the_crds():
    assert gate({"_cnpg_clusters": {"resources": []}, "_cnpg_foreign": ["tenant-a/orders"]}) is False
    assert gate({"_cnpg_clusters": {"resources": []}, "_cnpg_foreign": []}) is True


def test_a_failed_cluster_lookup_keeps_the_crds():
    """failed_when: false also zeroes result.failed, so only a missing key shows it."""
    failed = {"failed": False, "msg": "Failed to list Cluster: forbidden"}
    assert gate({"_cnpg_clusters": failed, "_cnpg_foreign": []}) is False
    assert cond(task(_CNPG, "Keep the CNPG CRDs")["when"], {"_cnpg_clusters": failed, "_cnpg_foreign": []}) is True


def test_the_marketplace_operator_keeps_everything():
    block = parent_of(_CNPG, "Delete the CNPG CRDs")
    assert cond(block["when"], {"_postgresql_from_olm": True}) is False
    assert cond(block["when"], {"_postgresql_from_olm": False}) is True
    # the namespace delete and the helm uninstall are inside the same gate
    for name in ("Uninstall CNPG operator Helm release", "Delete CNPG operator namespace"):
        assert task(block["block"], name)


def test_listing_the_crds_cannot_abort_the_teardown():
    assert task(_CNPG, "List the CNPG CRDs")["failed_when"] is False
    assert task(_CNPG, "List CNPG Clusters in every namespace")["failed_when"] is False


def test_nri_deletes_only_the_balloons_crd_and_only_its_own_install():
    crd_task = task(_NRI, "Delete the BalloonsPolicy CRD")
    assert crd_task["kubernetes.core.k8s"]["name"] == "balloonspolicies.config.nri"
    assert crd_task["failed_when"] is False
    # gated on the role's own Helm release, so an NRI install the admin manages stays
    assert "_nri_release.status is defined" in parent_of(_NRI, "Delete the BalloonsPolicy CRD")["when"]
