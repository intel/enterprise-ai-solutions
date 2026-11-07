# Copyright (C) 2024-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — checks the OpenShift prerequisites against what preflight read.

Registered as Jinja filters via FilterModule (bottom). Auto-loaded from
filter_plugins/ dir configured in ansible.cfg.

Usage in playbooks/includes/preflight_openshift.yaml:
    {{ openshift_prerequisites | openshift_prereq_select(planned_layers) }}
    {{ needed | openshift_prereq_report(platform=..., distribution_version=..., apis=...,
                                        subscriptions=..., extensions=..., csvs=...,
                                        installplans=..., instances=..., storage=...,
                                        strict=...) }}

The inputs are k8s_info results as registered (failed_when: false), so a read that
failed carries `msg` instead of `resources`. A read the identity is not allowed to
make makes the check UNVERIFIED (a warning), never OK: the installer must not claim
a requirement it could not see.

Each report row: {name, title, doc, level: ok|warn|fail, status, required,
details: [every line], problems: [only the lines behind a warn/fail]}.
"""

import re

OK, WARN, FAIL = "ok", "warn", "fail"
_RANK = {OK: 0, WARN: 1, FAIL: 2}


def _truthy(val):
    if isinstance(val, bool):
        return val
    if val is None:
        return True
    return str(val).strip().lower() in ("true", "yes", "on", "1")


def _read_error(res):
    """None when the read worked; else 'forbidden' or the first line of the error."""
    if not isinstance(res, dict) or "resources" in res:
        return None
    msg = str(res.get("msg") or res.get("reason") or "not read")
    if "Forbidden" in msg or "403" in msg.split("\n", 1)[0] or "forbidden" in msg:
        return "forbidden"
    return msg.strip().splitlines()[0] if msg.strip() else "not read"


def _get(obj, path):
    for key in path.split("."):
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _conditions(obj):
    return {c.get("type"): c for c in (_get(obj, "status.conditions") or [])}


def _version_tuple(text):
    m = re.match(r"v?(\d+(?:\.\d+)*)", str(text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def compare_version(installed, tested):
    """-1 older, 0 same, 1 newer, None unparsable. Compared at the precision of `tested`."""
    have, want = _version_tuple(installed), _version_tuple(tested)
    if have is None or want is None:
        return None
    have = (have + (0,) * len(want))[:len(want)]
    return (have > want) - (have < want)


class _Row(object):
    def __init__(self, req):
        self.req = req
        self.level = OK
        self.status = "OK"
        self.details = []
        self.problems = []
        self.required = _truthy(req.get("required", True))

    def add(self, level, status, detail):
        if level == FAIL and not self.required:
            level = WARN
        if _RANK[level] > _RANK[self.level]:
            self.level, self.status = level, status
        self.details.append(detail)
        self.problems.append(detail)

    def note(self, detail):
        self.details.append(detail)

    def hint(self, detail):
        """A how-to-fix line: goes with the problems when the row has any, else a note."""
        self.details.append(detail)
        if self.level != OK:
            self.problems.append(detail)

    def result(self):
        return {"name": self.req.get("name"), "title": self.req.get("title", self.req.get("name")),
                "doc": self.req.get("doc", ""), "level": self.level, "status": self.status,
                "required": self.required, "details": self.details, "problems": self.problems}


def _check_version(row, what, installed, tested, strict):
    if not tested:
        row.note("%s %s" % (what, installed or "?"))
        return None
    cmp = compare_version(installed, tested)
    if cmp is None:
        row.add(WARN, "UNVERIFIED", "%s version '%s' is not comparable with tested %s" % (what, installed, tested))
    elif cmp < 0:
        row.add(FAIL, "TOO OLD", "%s %s is older than the tested %s" % (what, installed, tested))
    elif cmp > 0:
        row.add(FAIL if strict else WARN, "NEWER",
                "%s %s is newer than the tested %s (not validated)" % (what, installed, tested))
    else:
        row.note("%s %s" % (what, installed))
    return cmp


def _pending_plan(sub, installplans):
    """(namespace, name, [CSVs]) of the unapproved InstallPlan the Subscription waits on, else None."""
    ns, name = _get(sub, "metadata.namespace"), _get(sub, "status.installPlanRef.name")
    if not name:
        return None
    res = next((r for r in (installplans or {}).get("results") or []
                if _get(r, "item.metadata.namespace") == ns
                and _get(r, "item.status.installPlanRef.name") == name), None)
    plan = ((res or {}).get("resources") or [None])[0]
    if plan is None or _get(plan, "spec.approved") is not False:
        return None
    return ns, name, _get(plan, "spec.clusterServiceVersionNames") or []


def _approve_hint(plan):
    ns, name, _csvs = plan
    return ("approve it: oc patch installplan %s -n %s --type merge -p '{\"spec\":{\"approved\":true}}'"
            % (name, ns))


def _check_apis(row, req, apis):
    if not apis:
        return
    missing = [a for a in req.get("apis") or [] if a not in apis]
    if missing:
        row.add(FAIL, "MISSING", "the cluster does not serve %s" % ", ".join(missing))


def _check_package(row, req, subscriptions, extensions, csvs, installplans, strict):
    package = req.get("package")
    if not package:
        return
    want_ns = req.get("namespace") or ""
    sub_err = _read_error(subscriptions)
    subs = [s for s in (subscriptions or {}).get("resources") or []
            if _get(s, "spec.name") == package]
    exts = [e for e in (extensions or {}).get("resources") or []
            if _get(e, "spec.source.catalog.packageName") == package]

    if not subs and not exts:
        if sub_err:
            row.add(WARN, "UNVERIFIED", "cannot list Subscriptions (%s): version of %s not checked" % (sub_err, package))
        else:
            channel = req.get("channel")
            row.add(FAIL, "MISSING", "operator %s is not installed (no Subscription)%s"
                    % (package, "; subscribe to channel %s" % channel if channel else ""))
        return

    if exts and not subs:
        ext = exts[0]
        ns = _get(ext, "spec.namespace") or ""
        if want_ns and ns != want_ns:
            row.add(FAIL, "WRONG NAMESPACE", "%s is installed into %s, it must be in %s" % (package, ns, want_ns))
        installed = _conditions(ext).get("Installed", {})
        if installed.get("status") != "True":
            row.add(FAIL, "NOT READY", "ClusterExtension %s is not Installed: %s"
                    % (_get(ext, "metadata.name"), installed.get("message", "no Installed condition")))
            return
        _check_version(row, package, _get(ext, "status.install.bundle.version"), req.get("version"), strict)
        return

    sub = next((s for s in subs if _get(s, "metadata.namespace") == want_ns), subs[0])
    ns = _get(sub, "metadata.namespace")
    if want_ns and ns != want_ns:
        row.add(FAIL, "WRONG NAMESPACE",
                "%s is installed in %s, it must be in %s (the operator watches only its own namespace)"
                % (package, ns, want_ns))
    csv_name = _get(sub, "status.installedCSV")
    plan = _pending_plan(sub, installplans)
    if not csv_name:
        if plan:
            row.add(FAIL, "NOT INSTALLED",
                    "Subscription %s/%s has no installed CSV: InstallPlan %s (%s) is waiting for approval"
                    % (ns, _get(sub, "metadata.name"), plan[1], ", ".join(plan[2]) or "?"))
            row.hint(_approve_hint(plan))
        else:
            row.add(FAIL, "NOT INSTALLED",
                    "Subscription %s/%s has no installed CSV (state: %s); approve its InstallPlan"
                    % (ns, _get(sub, "metadata.name"), _get(sub, "status.state") or "none"))
        return

    csv_res = next((r for r in (csvs or {}).get("results") or []
                    if _get(r, "item.metadata.namespace") == ns
                    and _get(r, "item.status.installedCSV") == csv_name), None)
    csv_err = _read_error(csv_res) if csv_res is not None else "not read"
    if csv_err:
        row.add(WARN, "UNVERIFIED", "cannot read CSV %s/%s (%s)" % (ns, csv_name, csv_err))
        return
    csv = (csv_res.get("resources") or [None])[0]
    if csv is None:
        row.add(FAIL, "MISSING", "CSV %s/%s named by the Subscription does not exist" % (ns, csv_name))
        return
    phase = _get(csv, "status.phase")
    if phase != "Succeeded":
        row.add(FAIL, "NOT READY", "CSV %s/%s is %s: %s"
                % (ns, csv_name, phase or "unknown", _get(csv, "status.message") or ""))
    _check_version(row, csv_name, _get(csv, "spec.version"), req.get("version"), strict)
    upgrade = [c for c in (plan or (None, None, []))[2] if c != csv_name]
    if upgrade:
        row.hint("an upgrade to %s is waiting: InstallPlan %s/%s is not approved; %s"
                 % (", ".join(upgrade), plan[0], plan[1], _approve_hint(plan)))


def _instance_mismatches(obj, spec):
    out = []
    conds = _conditions(obj)
    for ctype, want in (spec.get("conditions") or {}).items():
        cond = conds.get(ctype)
        if cond is None:
            out.append("%s missing" % ctype)
        elif str(cond.get("status")) != str(want):
            msg = cond.get("message") or cond.get("reason") or ""
            out.append("%s=%s%s" % (ctype, cond.get("status"), (" (%s)" % msg) if msg else ""))
    for path, want in (spec.get("fields") or {}).items():
        have = _get(obj, path)
        if str(have) != str(want):
            out.append("%s=%s, want %s" % (path, have, want))
    return out


def _describe(spec):
    where = spec.get("kind", "?")
    if spec.get("name"):
        where += " " + spec["name"]
    if spec.get("namespace"):
        where += " in " + spec["namespace"]
    return where


def _check_instances(row, req, instances):
    results = (instances or {}).get("results") or []
    for spec in req.get("instances") or []:
        res = next((r for r in results
                    if isinstance(r.get("item"), (list, tuple)) and len(r["item"]) == 2
                    and r["item"][0].get("name") == req.get("name") and r["item"][1] == spec), None)
        if res is None or res.get("skipped"):
            continue
        err = _read_error(res)
        if err == "forbidden":
            row.add(WARN, "UNVERIFIED", "cannot read %s (forbidden)" % _describe(spec))
            continue
        if err:
            row.add(FAIL, "MISSING", "no %s (%s)" % (_describe(spec), err))
            continue
        objs = res.get("resources") or []
        if not objs:
            row.add(FAIL, "MISSING", "no %s; create it as the doc describes" % _describe(spec))
            continue
        checked = [(o, _instance_mismatches(o, spec)) for o in objs]
        good = [o for o, bad in checked if not bad]
        if good:
            o = good[0]
            ns = _get(o, "metadata.namespace")
            row.note("%s %s%s ready" % (spec.get("kind"), (ns + "/") if ns else "", _get(o, "metadata.name")))
        else:
            o, bad = checked[0]
            row.add(FAIL, "NOT READY", "%s %s: %s" % (spec.get("kind"), _get(o, "metadata.name"), "; ".join(bad)))


def _is_default(sc):
    ann = _get(sc, "metadata.annotations") or {}
    return ann.get("storageclass.kubernetes.io/is-default-class") == "true"


def _check_storage(row, storage):
    storage = storage or {}
    wanted = storage.get("class") or ""
    listed = storage.get("list") or {}
    err = _read_error(listed)
    if not err:
        classes = listed.get("resources") or []
        names = [_get(sc, "metadata.name") for sc in classes]
        if wanted:
            if wanted in names:
                row.note("StorageClass %s (storage_custom_storageclass)" % wanted)
            else:
                row.add(FAIL, "MISSING", "storage_custom_storageclass=%s does not exist (have: %s)"
                        % (wanted, ", ".join(names) or "none"))
            return
        defaults = [sc for sc in classes if _is_default(sc)]
        if not defaults:
            row.add(FAIL, "MISSING", "no default StorageClass (have: %s); mark one with "
                    "storageclass.kubernetes.io/is-default-class=true" % (", ".join(names) or "none"))
        elif len(defaults) > 1:
            row.add(WARN, "AMBIGUOUS", "%d default StorageClasses: %s"
                    % (len(defaults), ", ".join(_get(sc, "metadata.name") for sc in defaults)))
        else:
            sc = defaults[0]
            row.note("default StorageClass %s (%s, %s)" % (_get(sc, "metadata.name"), sc.get("provisioner"),
                                                           sc.get("volumeBindingMode", "Immediate")))
        return

    probe = storage.get("probe")
    if not probe:
        row.add(WARN, "UNVERIFIED", "cannot list StorageClasses (%s) and the PVC probe is off "
                "(openshift_prereq_storage_probe)" % err)
        return
    row.note("cannot list StorageClasses (%s); checked with a probe PVC" % err)
    probe_err = _read_error(probe.get("pvc"))
    pvc = ((probe.get("pvc") or {}).get("resources") or [None])[0]
    if probe_err or pvc is None:
        row.add(WARN, "UNVERIFIED", "the probe PVC could not be created: %s"
                % (probe.get("error") or probe_err or "not found"))
        return
    sc_name = _get(pvc, "spec.storageClassName")
    if not sc_name:
        row.add(FAIL, "MISSING", "no default StorageClass: the probe PVC got no storageClassName")
        return
    phase = _get(pvc, "status.phase")
    events = sorted((probe.get("events") or {}).get("resources") or [],
                    key=lambda e: str(e.get("lastTimestamp") or e.get("eventTime") or ""))
    reasons = [e.get("reason") for e in events]
    if phase == "Bound":
        row.note("StorageClass %s: probe PVC Bound" % sc_name)
    elif "ProvisioningFailed" in reasons:
        msg = [e.get("message") for e in events if e.get("reason") == "ProvisioningFailed"][-1]
        row.add(FAIL, "NOT WORKING", "StorageClass %s cannot provision: %s" % (sc_name, msg))
    elif "WaitForFirstConsumer" in reasons:
        row.note("StorageClass %s (WaitForFirstConsumer: provisioning is checked when the first pod starts)"
                 % sc_name)
    else:
        last = events[-1].get("message") if events else "no events"
        row.add(FAIL, "NOT WORKING", "StorageClass %s: probe PVC still %s after %ss (%s)"
                % (sc_name, phase or "Pending", probe.get("timeout", "?"), last))


def openshift_prereq_select(prereqs, planned_layers):
    """Entries the plan needs: a layer in planned_layers, and `when` (rendered) true."""
    planned = set(planned_layers or [])
    return [r for r in prereqs or []
            if planned.intersection(r.get("layers") or []) and _truthy(r.get("when", True))]


def openshift_prereq_report(prereqs, platform=None, distribution_version="", apis=None,
                            subscriptions=None, extensions=None, csvs=None, installplans=None,
                            instances=None, storage=None, strict=False):
    rows = []
    if platform:
        row = _Row(platform)
        _check_version(row, "OpenShift", distribution_version, platform.get("version"), _truthy(strict))
        rows.append(row.result())
    for req in prereqs or []:
        row = _Row(req)
        _check_apis(row, req, apis or [])
        _check_package(row, req, subscriptions, extensions, csvs, installplans, _truthy(strict))
        _check_instances(row, req, instances)
        if _truthy(req.get("storage", False)):
            _check_storage(row, storage)
        rows.append(row.result())
    return rows


class FilterModule(object):
    def filters(self):
        return {
            "openshift_prereq_select": openshift_prereq_select,
            "openshift_prereq_report": openshift_prereq_report,
        }
