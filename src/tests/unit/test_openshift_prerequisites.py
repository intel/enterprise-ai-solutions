# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the openshift_prerequisites filter plugin."""

import pytest

from plugin_loader import load_plugin

op = load_plugin("openshift_prerequisites")

NS = "operators"
FORBIDDEN = {"msg": "pods is forbidden: User cannot list resource"}


def report(req, **kwargs):
    """The single report row for one requirement."""
    return op.openshift_prereq_report([dict({"name": "r"}, **req)], **kwargs)[0]


def sub(csv="pkg.v1.2.0", plan=None, ns=NS, package="pkg"):
    status = {}
    if csv:
        status["installedCSV"] = csv
    if plan:
        status["installPlanRef"] = {"name": plan}
    return {"metadata": {"name": "pkg", "namespace": ns}, "spec": {"name": package}, "status": status}


def csv_result(subscription, version="1.2.0", phase="Succeeded"):
    return {"item": subscription,
            "resources": [{"status": {"phase": phase}, "spec": {"version": version}}]}


def plan_result(subscription, approved, csvs=()):
    plan = {"spec": {"approved": approved, "clusterServiceVersionNames": list(csvs)}}
    return {"item": subscription, "resources": [plan]}


@pytest.mark.parametrize("installed, tested, expected", [
    ("4.18.3", "4.18", 0),       # compared at the precision of `tested`
    ("v1.3.0", "1.2.0", 1),
    ("1.2", "1.2.1", -1),
    ("latest", "1.2", None),
    ("1.2", "", None),
])
def test_compare_version(installed, tested, expected):
    assert op.compare_version(installed, tested) == expected


def test_pending_plan_unapproved():
    s = sub(csv=None, plan="install-abc")
    plans = {"results": [plan_result(s, False, ["pkg.v2.0.0"])]}
    assert op._pending_plan(s, plans) == (NS, "install-abc", ["pkg.v2.0.0"])


@pytest.mark.parametrize("plans", [
    {"results": [plan_result(sub(plan="install-abc"), True)]},   # already approved
    {"results": []},
    None,
])
def test_pending_plan_none(plans):
    assert op._pending_plan(sub(plan="install-abc"), plans) is None


def test_pending_plan_without_reference():
    assert op._pending_plan(sub(), {"results": [plan_result(sub(), False)]}) is None


def test_apis_present_is_ok():
    row = report({"apis": ["a.io/v1"]}, apis=["a.io/v1", "b.io/v1"])
    assert row["level"] == op.OK


def test_apis_absent_is_missing():
    row = report({"apis": ["a.io/v1", "c.io/v1"]}, apis=["a.io/v1"])
    assert (row["level"], row["status"]) == (op.FAIL, "MISSING")
    assert "c.io/v1" in row["problems"][0] and "a.io/v1" not in row["problems"][0]


@pytest.mark.parametrize("apis", [[], None])
def test_apis_not_listed_is_unverified(apis):
    row = report({"apis": ["a.io/v1"]}, apis=apis)
    assert (row["level"], row["status"]) == (op.WARN, "UNVERIFIED")


def test_apis_not_required_row_downgrades_fail():
    row = report({"apis": ["a.io/v1"], "required": False}, apis=["b.io/v1"])
    assert (row["level"], row["status"]) == (op.WARN, "MISSING")


def test_package_without_subscription_is_missing():
    row = report({"package": "pkg", "channel": "stable"}, subscriptions={"resources": []})
    assert (row["level"], row["status"]) == (op.FAIL, "MISSING")
    assert "channel stable" in row["problems"][0]


def test_package_unreadable_subscriptions_is_unverified():
    row = report({"package": "pkg"}, subscriptions=FORBIDDEN)
    assert (row["level"], row["status"]) == (op.WARN, "UNVERIFIED")
    assert "forbidden" in row["problems"][0]


def package_report(version, tested="1.2.0", strict=False, phase="Succeeded"):
    s = sub()
    return report({"package": "pkg", "namespace": NS, "version": tested},
                  subscriptions={"resources": [s]}, csvs={"results": [csv_result(s, version, phase)]},
                  strict=strict)


def test_package_tested_version_is_ok():
    row = package_report("1.2.0")
    assert (row["level"], row["problems"]) == (op.OK, [])


def test_package_newer_warns_and_fails_when_strict():
    assert (package_report("1.3.0")["level"], package_report("1.3.0")["status"]) == (op.WARN, "NEWER")
    assert (package_report("1.3.0", strict=True)["level"], package_report("1.3.0", strict=True)["status"]) \
        == (op.FAIL, "NEWER")


def test_package_too_old_fails():
    row = package_report("1.1.0")
    assert (row["level"], row["status"]) == (op.FAIL, "TOO OLD")


def test_package_csv_not_succeeded_is_not_ready():
    row = package_report("1.2.0", phase="Failed")
    assert (row["level"], row["status"]) == (op.FAIL, "NOT READY")


def test_package_in_wrong_namespace():
    s = sub(ns="other")
    row = report({"package": "pkg", "namespace": NS}, subscriptions={"resources": [s]},
                 csvs={"results": [csv_result(s)]})
    assert (row["level"], row["status"]) == (op.FAIL, "WRONG NAMESPACE")


def test_package_waiting_for_approval_gets_hint():
    s = sub(csv=None, plan="install-abc")
    row = report({"package": "pkg", "namespace": NS}, subscriptions={"resources": [s]},
                 installplans={"results": [plan_result(s, False, ["pkg.v1.2.0"])]})
    assert (row["level"], row["status"]) == (op.FAIL, "NOT INSTALLED")
    assert "oc patch installplan install-abc -n operators" in row["problems"][-1]


def test_package_unreadable_csv_is_unverified():
    s = sub()
    row = report({"package": "pkg", "namespace": NS, "version": "1.2.0"},
                 subscriptions={"resources": [s]}, csvs={"results": [{"item": s, **FORBIDDEN}]})
    assert (row["level"], row["status"]) == (op.WARN, "UNVERIFIED")


SPEC = {"kind": "Thing", "name": "t", "namespace": "ns", "conditions": {"Ready": "True"}}


def instance_report(result):
    req = {"name": "r", "instances": [SPEC]}
    return op.openshift_prereq_report([req], instances={"results": [dict({"item": [req, SPEC]}, **result)]})[0]


def thing(status="True"):
    return {"metadata": {"name": "t", "namespace": "ns"},
            "status": {"conditions": [{"type": "Ready", "status": status, "message": "boom"}]}}


def test_instance_ready_is_ok():
    row = instance_report({"resources": [thing()]})
    assert (row["level"], row["problems"]) == (op.OK, [])
    assert "ready" in row["details"][0]


def test_instance_missing():
    row = instance_report({"resources": []})
    assert (row["level"], row["status"]) == (op.FAIL, "MISSING")


def test_instance_condition_not_met():
    row = instance_report({"resources": [thing("False")]})
    assert (row["level"], row["status"]) == (op.FAIL, "NOT READY")
    assert "Ready=False (boom)" in row["problems"][0]


def test_instance_forbidden_read_is_unverified():
    row = instance_report(FORBIDDEN)
    assert (row["level"], row["status"]) == (op.WARN, "UNVERIFIED")


def test_instance_skipped_is_not_checked():
    assert instance_report({"skipped": True})["level"] == op.OK


def storage_report(storage):
    return report({"storage": True}, storage=storage)


def storage_class(name="fast", default=True):
    ann = {"storageclass.kubernetes.io/is-default-class": "true"} if default else {}
    return {"metadata": {"name": name, "annotations": ann}, "provisioner": "csi.example"}


def test_storage_default_class_is_ok():
    row = storage_report({"list": {"resources": [storage_class()]}})
    assert (row["level"], row["problems"]) == (op.OK, [])


def test_storage_no_default_class_fails():
    row = storage_report({"list": {"resources": [storage_class(default=False)]}})
    assert (row["level"], row["status"]) == (op.FAIL, "MISSING")


def test_storage_two_defaults_is_ambiguous():
    row = storage_report({"list": {"resources": [storage_class("a"), storage_class("b")]}})
    assert (row["level"], row["status"]) == (op.WARN, "AMBIGUOUS")


def test_storage_custom_class():
    listed = {"resources": [storage_class("fast")]}
    assert storage_report({"class": "fast", "list": listed})["level"] == op.OK
    assert storage_report({"class": "nope", "list": listed})["status"] == "MISSING"


def test_storage_list_forbidden_without_probe_is_unverified():
    row = storage_report({"list": FORBIDDEN})
    assert (row["level"], row["status"]) == (op.WARN, "UNVERIFIED")


def probe(phase, events=(), class_name="fast"):
    pvc = {"spec": {"storageClassName": class_name}, "status": {"phase": phase}}
    return {"list": FORBIDDEN, "probe": {"pvc": {"resources": [pvc]},
                                         "events": {"resources": [dict(e) for e in events]}}}


def test_storage_probe_bound_is_ok():
    row = storage_report(probe("Bound"))
    assert (row["level"], row["problems"]) == (op.OK, [])


def test_storage_probe_wait_for_first_consumer_is_ok():
    row = storage_report(probe("Pending", [{"reason": "WaitForFirstConsumer"}]))
    assert (row["level"], row["problems"]) == (op.OK, [])


def test_storage_probe_provisioning_failed():
    row = storage_report(probe("Pending", [{"reason": "ProvisioningFailed", "message": "no space"}]))
    assert (row["level"], row["status"]) == (op.FAIL, "NOT WORKING")
    assert "no space" in row["problems"][0]


def test_storage_probe_pvc_without_class_means_no_default():
    row = storage_report(probe("Pending", class_name=None))
    assert (row["level"], row["status"]) == (op.FAIL, "MISSING")


PREREQS = [
    {"name": "a", "layers": ["platform"]},
    {"name": "b", "layers": ["inference", "platform"], "when": False},
    {"name": "c", "layers": ["inference"], "when": "True"},
    {"name": "d", "layers": ["inference"], "when": "false"},
    {"name": "e"},
]


@pytest.mark.parametrize("layers, expected", [
    (["platform"], ["a"]),
    (["inference"], ["c"]),
    (["platform", "inference"], ["a", "c"]),
    ([], []),
    (None, []),
])
def test_select_by_layer_and_when(layers, expected):
    assert [r["name"] for r in op.openshift_prereq_select(PREREQS, layers)] == expected
