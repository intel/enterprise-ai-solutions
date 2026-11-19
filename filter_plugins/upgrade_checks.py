# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — the layer deployment record, the run class of an
install, the gating of upgrade-check verdicts and the data-consistency
comparison of a layer's stores before and after the run.

Used by roles/upgrade_checks and playbooks/includes/run_layer.yaml.

A record a layer still writes itself (Intel Chatbot Toolkit 3.0.0, layer
erag) nests its state under deployment: and has no top-level layer. It is lifted
to the top level on read; core writes over it only once the layer declares
supports.upgrade, the release that stops writing its own.
"""

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

RECORD_FIELDS = ("layer", "version", "targetVersion", "status", "startedAt",
                 "completedAt", "forced", "forcedChecks", "failedComponent",
                 "haltReason", "dependencies", "installedBy")
UPGRADE_CLASSES = ("upgrade", "downgrade")


def layer_record_is_legacy(raw):
    raw = raw or {}
    return "layer" not in raw and isinstance(raw.get("deployment"), dict)


def record_owned_by_layer(raw, supports_upgrade):
    """Whether the layer still writes this record itself, so core must leave it alone."""
    return layer_record_is_legacy(raw) and not supports_upgrade


def lift_layer_record(raw):
    """Keep only core's top-level fields; a legacy record's deployment: block becomes them."""
    raw = raw or {}
    legacy = layer_record_is_legacy(raw)
    source = raw["deployment"] if legacy else raw
    # A legacy writer's installedBy is its own field, not core's mapping.
    return {key: source[key] for key in RECORD_FIELDS
            if key in source and not (legacy and key == "installedBy")}


def claim_layer_dirs(layer_dirs, manifest, repo_dir):
    """Add one repository's components.yaml: it owns the layers its layers: declares, and the
    layers of its components that no earlier repository claimed (ai-inference adds components
    to a core layer)."""
    manifest = manifest or {}
    claimed = {component["layer"]: repo_dir for component in manifest.get("components") or []}
    claimed.update(layer_dirs or {})
    claimed.update({layer["name"]: repo_dir for layer in manifest.get("layers") or []})
    return claimed


def layer_fully_planned(layer_components, planned_components):
    """Whether the run holds every component of the layer, so it may write the layer's record."""
    return not set(layer_components) - set(planned_components)


def layer_release_version(data):
    """The version a layer's version.yaml declares: layer_version, else solution_version."""
    data = data or {}
    version = data.get("layer_version") or data.get("solution_version")
    return None if version is None else str(version)


def _version(value, layer):
    try:
        return Version(str(value).split("+")[0])
    except InvalidVersion:
        raise ValueError("layer %s: %r is not a version" % (layer, value)) from None


def classify_run(record, release, layer):
    """The run class of an install: fresh | reinstall | resume | upgrade | downgrade | conflict | unversioned."""
    record = record or {}
    status = record.get("status")
    if status == "deleting":
        return "conflict"
    # Parsed before a fresh or unversioned record decides, so a release that is not a version is never recorded.
    wanted = _version(release, layer) if release else None
    if not record:
        return "fresh" if release else "unversioned"
    if not release:
        return "unversioned"
    # A version that does not parse is judged by the checks, not here.
    if status in ("in-progress", "halted"):
        target = record.get("targetVersion")
        if not target:
            return "conflict"
        try:
            return "resume" if _version(target, layer) == wanted else "conflict"
        except ValueError:
            return "conflict"
    if not record.get("version"):
        return "fresh"
    recorded = _version(record["version"], layer)
    if wanted == recorded:
        return "reinstall"
    return "upgrade" if wanted > recorded else "downgrade"


def version_change_verdict(run_class, layer, record, release, env="<env>"):
    record = record or {}
    verdict = {"layer": layer, "check": "version-change", "verdict": "pass",
               "reason": run_class, "resolve": None}
    if run_class == "unversioned":
        verdict.update(verdict="skipped", reason="no version declared for %s" % layer)
    elif run_class == "downgrade":
        verdict.update(verdict="blocking",
                       reason="downgrade %s -> %s" % (record.get("version"), release),
                       resolve="install %s from a release at %s or later" % (layer, record.get("version")))
    elif run_class == "conflict" and record.get("status") == "deleting":
        verdict.update(verdict="blocking", reason="a teardown of %s did not finish" % layer,
                       resolve="./es_auto_installer.sh teardown %s --env %s" % (layer, env))
    elif run_class == "conflict":
        verdict.update(verdict="blocking",
                       reason="a run towards %s is %s; this release is %s"
                              % (record.get("targetVersion"), record.get("status"), release),
                       resolve="finish it: install %s from a release at %s" % (layer, record.get("targetVersion")))
    return verdict


def cluster_probe_verdict(layer, reason):
    """The verdict for one planned layer when the deployment-record probe could not read the
    cluster: blocking, so a layer that declares supports.upgrade refuses the run instead of
    running every check as if the layer were a fresh install."""
    return {"layer": layer, "check": "cluster-probe", "verdict": "blocking",
            "reason": "the cluster did not answer (%s)" % reason,
            "resolve": "check the cluster connection and retry, or add --force to install as fresh"}


def _upgrade_range(expr):
    """A bare version means that version or later. Only a string is a range: YAML reads an
    unquoted 1.10 as the float 1.1."""
    if not isinstance(expr, str):
        raise InvalidSpecifier(repr(expr))
    expr = expr.strip()
    if expr[:1] not in "><=!~":
        expr = ">=" + expr
    return SpecifierSet(expr)


def upgrade_path_verdict(version_data, layer, record, run_class, release, env="<env>"):
    """Whether the recorded version lies in the release's upgrade_base_version range."""
    record, version_data = record or {}, version_data or {}
    recorded = record.get("version")
    base = version_data.get("upgrade_base_version")
    verdict = {"layer": layer, "check": "upgrade-path", "verdict": "pass", "reason": None, "resolve": None}
    if run_class == "conflict" and record.get("status") == "deleting":
        verdict.update(verdict="skipped", reason="a teardown of %s did not finish" % layer)
        return verdict
    if run_class == "conflict" and not recorded:
        verdict.update(verdict="skipped", reason="%s has no recorded version to upgrade from" % layer)
        return verdict
    if base is None or str(base).strip() == "":
        verdict.update(verdict="skipped", reason="%s declares no upgrade_base_version" % layer)
        return verdict
    try:
        recorded_version = _version(recorded, layer)
    except ValueError as err:
        verdict.update(verdict="blocking", reason="recorded version: %s" % err,
                       resolve="install %s from a release whose upgrade_base_version covers %s,"
                               " then this one (--env %s)" % (layer, recorded, env))
        return verdict
    returning = run_class == "conflict" and _version(release, layer) <= recorded_version
    if run_class == "downgrade" or returning:
        verdict.update(verdict="skipped",
                       reason="%s %s -> %s is no upgrade; the version-change check judges it" % (layer, recorded, release))
        return verdict
    if str(base).strip().lower() == "unsupported":
        verdict.update(verdict="blocking", reason="this release of %s does not upgrade from an earlier one" % layer,
                       resolve="stay on the recorded release, or tear %s down and install it fresh (--env %s)" % (layer, env))
        return verdict
    try:
        upgrade_range = _upgrade_range(base)
    except InvalidSpecifier:
        verdict.update(verdict="blocking", reason="upgrade_base_version %r of %s is not a range" % (base, layer),
                       resolve="fix upgrade_base_version in the version.yaml of %s" % layer)
        return verdict
    if upgrade_range.contains(recorded_version, prereleases=True):
        verdict.update(reason="%s is in %s" % (recorded, base))
    else:
        verdict.update(verdict="blocking", reason="%s %s is outside upgrade_base_version %s" % (layer, recorded, base),
                       resolve="install %s from a release whose upgrade_base_version covers %s, then this one"
                               " (--env %s)" % (layer, recorded, env))
    return verdict


def data_consistency_present_verdict(layer, supports_upgrade, present, error=None):
    """Refuse before any change if a layer gating on upgrade ships no usable data_consistency.yaml;
    a layer that does not gate is skipped, same as the post-run data-consistency verdict is."""
    verdict = {"layer": layer, "check": "data-consistency-present", "verdict": "pass",
               "reason": "%s ships data_consistency.yaml" % layer, "resolve": None}
    if not supports_upgrade:
        verdict.update(verdict="skipped", reason="%s does not declare supports.upgrade" % layer)
    elif not present:
        verdict.update(verdict="blocking",
                       reason="%s declares supports.upgrade but ships no data_consistency.yaml" % layer,
                       resolve="add data_consistency.yaml next to %s's components.yaml "
                               "(format: docs/reference/adding_solutions.md), or rerun with --force" % layer)
    elif error:
        verdict.update(verdict="blocking", reason=error,
                       resolve="fix the data_consistency.yaml of %s "
                               "(format: docs/reference/adding_solutions.md), or rerun with --force" % layer)
    return verdict


def data_consistency_resume_verdicts(layers, layer_class, records):
    """A resume of a run the data-consistency check halted is refused like the halt itself,
    so re-running the same command never clears it; --force accepts the loss.
    The check id is the post-run check's own, so one --force covers the resume and its new count."""
    return [{"layer": layer, "check": "data-consistency", "verdict": "blocking",
             "reason": "the last run of %s halted because it lost entries" % layer,
             "resolve": "restore the %s data from its backup, or rerun with --force to accept the loss" % layer}
            for layer in layers
            if layer_class.get(layer) == "resume" and (records.get(layer) or {}).get("haltReason") == "data-consistency"]


def consistency_probe_list(text, path):
    """The probes a data_consistency.yaml lists, and the error that makes it unusable, if any."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        return {"probes": [], "error": "%s does not parse: %s" % (path, " ".join(str(err).split()))}
    data = {} if data is None else data
    probes = (data.get("probes") or []) if isinstance(data, dict) else None
    if not isinstance(probes, list) or not all(isinstance(probe, str) for probe in probes):
        return {"probes": [], "error": "%s must set probes: to a list of task files" % path}
    return {"probes": probes, "error": None}


def upgrade_gate(verdicts, supports_upgrade, forced=False):
    """Mark each verdict gating or not; return what refuses the run and what --force bypassed.

    supports_upgrade maps a layer name to its supports.upgrade declaration.
    """
    checks = [dict(v, gating=bool(supports_upgrade.get(v["layer"])) and v["verdict"] == "blocking")
              for v in verdicts]
    gating = ["%s/%s" % (v["layer"], v["check"]) for v in checks if v["gating"]]
    return {
        "checks": checks,
        "refused": [] if forced else gating,
        "forcedChecks": gating if forced else [],
    }


def record_forced(raw, forced_checks, run_class, layer):
    """The record's forced/forcedChecks fields: this run's --force bypasses for layer, plus,
    on a resume, whatever bypassed the interrupted record's own in-progress run."""
    raw = raw or {}
    prefix = layer + "/"
    checks = {check for check in (forced_checks or []) if check.startswith(prefix)}
    if run_class == "resume":
        checks |= {check for check in (raw.get("forcedChecks") or []) if check.startswith(prefix)}
    return sorted(checks)


def record_dependencies(records, depends_on):
    """The recorded version of each lower layer, null where it has no record."""
    return {name: (records.get(name) or {}).get("version") for name in depends_on or []}


def record_version(release, raw):
    """The version a completed write records: the resolved release, or the one already on the
    record when this run could not resolve one (so a complete write never erases it)."""
    return release if release is not None else (raw or {}).get("version")


def check_layers(layer_class, check, every_class, also=()):
    """The planned layers a check judges: all for a check in every_class, else upgrade and
    downgrade runs, plus runs of the classes in also."""
    classes = UPGRADE_CLASSES + tuple(also)
    return [layer for layer, run_class in layer_class.items() if check in every_class or run_class in classes]


def report_due(run_class, record=None):
    """An upgrade or downgrade run, or the resume of one: the finishing run writes the report."""
    if run_class in UPGRADE_CLASSES:
        return True
    record = record or {}
    if run_class != "resume" or not record.get("version"):
        return False
    layer = record.get("layer")
    return _version(record["version"], layer) != _version(record["targetVersion"], layer)


# Data consistency: a layer's probes count its stores before and after the run.
# A count is an integer >= 0; a store a probe cannot count is {skipped: <reason>},
# never 0, so it is not compared and never reads as an empty store.

def _skip_reason(count):
    return count.get("skipped") if isinstance(count, dict) else None


def consistency_failed(probe, message):
    """The counts of a probe that failed: one store named after the probe file, marked failed."""
    return {probe: {"skipped": "probe %s failed: %s" % (probe, message), "failed": True}}


def consistency_counts(result, probe):
    """Validate what one probe set in data_consistency_counts; a bad value becomes skipped."""
    if not isinstance(result, dict):
        return consistency_failed(probe, "data_consistency_counts is not a mapping: %r" % (result,))
    counts = {}
    for store, count in result.items():
        if isinstance(count, dict) and str(count.get("skipped") or "").strip():
            counts[store] = {"skipped": str(count["skipped"])}
        elif str(count).isdecimal():
            counts[store] = int(count)
        else:
            counts[store] = {"skipped": "probe %s returned %r" % (probe, count)}
    return counts


def consistency_merge(counts, new, probe):
    """Add one probe's counts; a store an earlier probe already reported is skipped, never overwritten."""
    merged = dict(counts or {})
    for store, count in new.items():
        merged[store] = ({"skipped": "store reported by more than one probe, again by %s" % probe}
                         if store in merged else count)
    return merged


def pre_consistency_gate(counts_by_layer, supports_upgrade, forced=False):
    """The gate for the pre-run counts, same shape as upgrade_gate: blocking per layer whose
    pre-run snapshot has a failed entry, so a layer that declares supports.upgrade refuses the
    run before anything changes rather than comparing against counts that were never taken.
    --force accepts it and is recorded in forcedChecks."""
    verdicts = []
    for layer, counts in (counts_by_layer or {}).items():
        failed = sorted(store for store, count in (counts or {}).items() if isinstance(count, dict) and count.get("failed"))
        verdict = {"layer": layer, "check": "data-consistency-pre", "verdict": "pass", "reason": None, "resolve": None}
        if failed:
            verdict.update(verdict="blocking", reason="the pre-run count failed: %s" % ", ".join(failed),
                           resolve="fix the %s probe (docs/reference/adding_solutions.md), or rerun with --force" % layer)
        verdicts.append(verdict)
    return upgrade_gate(verdicts, supports_upgrade, forced)


def _compare_store(pre, post, failed_probes):
    reason = _skip_reason(pre) or _skip_reason(post)
    if reason is None and pre is None:
        reason = "not counted before the run"
    result = {"pre": pre if isinstance(pre, int) else None, "post": post if isinstance(post, int) else None}
    # A store counted before the run and missing after it is lost if a probe failed;
    # with every probe run, the release no longer declares it.
    if reason is None and post is None and failed_probes:
        return dict(result, verdict="blocking",
                    reason="not counted after the run: probe %s failed" % ", ".join(failed_probes))
    if reason is None and post is None:
        reason = "not counted after the run"
    if reason is not None:
        return dict(result, verdict="skipped", reason=reason)
    if post < pre:
        return dict(result, verdict="blocking")
    return dict(result, verdict="baseline" if post == 0 else "pass")


def compare_consistency(pre, post):
    pre, post = pre or {}, post or {}
    failed_probes = sorted(store for store, count in post.items() if isinstance(count, dict) and count.get("failed"))
    stores = list(pre) + [store for store in post if store not in pre]
    return {store: _compare_store(pre.get(store), post.get(store), failed_probes) for store in stores}


def consistency_verdict(pre, post, layer, supports_upgrade=False, no_pre_reason=None):
    """The layer's consistency verdict for the report; pre is None when nothing was counted
    before the run: the layer ships no probes, or no_pre_reason says why."""
    verdict = {"check": "data-consistency", "verdict": "skipped", "gating": False, "stores": {}}
    if pre is None:
        return dict(verdict, reason=no_pre_reason or "%s ships no data_consistency.yaml" % layer)
    stores = compare_consistency(pre, post)
    lost = ["%s: %s -> %s" % (name, s["pre"], "not counted" if s["post"] is None else s["post"])
            for name, s in stores.items() if s["verdict"] == "blocking"]
    counted = [s for s in stores.values() if s["verdict"] != "skipped"]
    if lost:
        return dict(verdict, verdict="blocking", reason="entries lost: " + ", ".join(lost),
                    gating=bool(supports_upgrade), stores=stores)
    if not counted:
        return dict(verdict, reason="no store counted", stores=stores)
    if all(s["verdict"] == "baseline" for s in counted):
        return dict(verdict, verdict="baseline", reason="every counted store is empty", stores=stores)
    return dict(verdict, verdict="pass", reason="no store lost entries", stores=stores)


class FilterModule:
    def filters(self):
        return {
            "lift_layer_record": lift_layer_record,
            "claim_layer_dirs": claim_layer_dirs,
            "layer_fully_planned": layer_fully_planned,
            "layer_record_is_legacy": layer_record_is_legacy,
            "record_owned_by_layer": record_owned_by_layer,
            "layer_release_version": layer_release_version,
            "classify_run": classify_run,
            "version_change_verdict": version_change_verdict,
            "cluster_probe_verdict": cluster_probe_verdict,
            "upgrade_path_verdict": upgrade_path_verdict,
            "data_consistency_present_verdict": data_consistency_present_verdict,
            "data_consistency_resume_verdicts": data_consistency_resume_verdicts,
            "upgrade_gate": upgrade_gate,
            "record_forced": record_forced,
            "record_dependencies": record_dependencies,
            "record_version": record_version,
            "check_layers": check_layers,
            "report_due": report_due,
            "consistency_probe_list": consistency_probe_list,
            "consistency_failed": consistency_failed,
            "consistency_counts": consistency_counts,
            "consistency_merge": consistency_merge,
            "pre_consistency_gate": pre_consistency_gate,
            "consistency_verdict": consistency_verdict,
        }
