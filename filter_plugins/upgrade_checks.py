# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — the layer deployment record, the run class of an
install, the gating of upgrade-check verdicts and the data-consistency
comparison of a layer's stores before and after the run.

Used by roles/upgrade_checks and playbooks/includes/run_layer.yaml.

A record a layer still writes itself (ERAG 3.0.0) nests its state under
deployment: and has no top-level layer. It is lifted to the top level on read;
core writes over it only once the layer declares supports.upgrade, the release
that stops writing its own.
"""

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
    version = data.get("layer_version", data.get("solution_version"))
    return None if version is None else str(version)


def _version(value, layer):
    try:
        return Version(str(value).split("+")[0])
    except InvalidVersion:
        raise ValueError("layer %s: %r is not a version" % (layer, value)) from None


def classify_run(record, release, layer):
    """The run class of an install: fresh | reinstall | resume | upgrade | downgrade | conflict | unversioned."""
    record = record or {}
    if not record:
        return "fresh" if release else "unversioned"
    status = record.get("status")
    if status == "deleting":
        return "conflict"
    if not release:
        return "unversioned"
    if status in ("in-progress", "halted"):
        target = record.get("targetVersion")
        return "resume" if target and _version(target, layer) == _version(release, layer) else "conflict"
    if not record.get("version"):
        return "fresh"
    recorded, wanted = _version(record["version"], layer), _version(release, layer)
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


def data_consistency_present_verdict(layer, supports_upgrade, present):
    """Refuse before any change if a layer gating on upgrade ships no data_consistency.yaml;
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
    return verdict


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
        "forced": bool(forced and gating),
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


def check_layers(layer_class, check, every_class):
    """The planned layers a check judges: all for a check in every_class, else upgrade and downgrade runs."""
    return [layer for layer, run_class in layer_class.items() if check in every_class or run_class in UPGRADE_CLASSES]


def report_due(run_class):
    return run_class in UPGRADE_CLASSES


# Data consistency: a layer's probes count its stores before and after the run.
# A count is an integer >= 0; a store a probe cannot count is {skipped: <reason>},
# never 0, so it is not compared and never reads as an empty store.

def _skip_reason(count):
    return count.get("skipped") if isinstance(count, dict) else None


def consistency_failed(probe, message):
    """The counts of a probe that failed: one store named after the probe file."""
    return {probe: {"skipped": "probe %s failed: %s" % (probe, message)}}


def consistency_counts(result, probe):
    """Validate what one probe set in data_consistency_counts; a bad value becomes skipped."""
    if not isinstance(result, dict):
        return consistency_failed(probe, "data_consistency_counts is not a mapping: %r" % (result,))
    counts = {}
    for store, count in result.items():
        if isinstance(count, dict) and str(count.get("skipped") or "").strip():
            counts[store] = {"skipped": str(count["skipped"])}
        elif str(count).isdigit():
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


def _compare_store(pre, post):
    reason = _skip_reason(pre) or _skip_reason(post)
    if reason is None and pre is None:
        reason = "not counted before the run"
    if reason is None and post is None:
        reason = "not counted after the run"
    result = {"pre": pre if isinstance(pre, int) else None, "post": post if isinstance(post, int) else None}
    if reason is not None:
        return dict(result, verdict="skipped", reason=reason)
    if post < pre:
        return dict(result, verdict="blocking")
    return dict(result, verdict="baseline" if post == 0 else "pass")


def compare_consistency(pre, post):
    pre, post = pre or {}, post or {}
    stores = list(pre) + [store for store in post if store not in pre]
    return {store: _compare_store(pre.get(store), post.get(store)) for store in stores}


def consistency_verdict(pre, post, layer, supports_upgrade=False):
    """The layer's consistency verdict for the report; pre is None when the layer ships no probes."""
    verdict = {"check": "data-consistency", "verdict": "skipped", "gating": False, "stores": {}}
    if pre is None:
        return dict(verdict, reason="%s ships no data_consistency.yaml" % layer)
    stores = compare_consistency(pre, post)
    lost = ["%s: %s -> %s" % (name, s["pre"], s["post"]) for name, s in stores.items() if s["verdict"] == "blocking"]
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
            "data_consistency_present_verdict": data_consistency_present_verdict,
            "upgrade_gate": upgrade_gate,
            "record_forced": record_forced,
            "record_dependencies": record_dependencies,
            "check_layers": check_layers,
            "report_due": report_due,
            "consistency_failed": consistency_failed,
            "consistency_counts": consistency_counts,
            "consistency_merge": consistency_merge,
            "compare_consistency": compare_consistency,
            "consistency_verdict": consistency_verdict,
        }
