# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — the layer deployment record, the run class of an
install, and the gating of upgrade-check verdicts.

Used by roles/upgrade_checks and playbooks/includes/run_layer.yaml.

A record a layer still writes itself (ERAG 3.0.0) nests its state under
deployment: and has no top-level layer. It is lifted to the top level on read;
core never writes over it.
"""

from packaging.version import InvalidVersion, Version

RECORD_FIELDS = ("layer", "version", "targetVersion", "status", "startedAt",
                 "completedAt", "forced", "forcedChecks", "failedComponent",
                 "dependencies", "installedBy")
UPGRADE_CLASSES = ("upgrade", "downgrade")


def layer_record_is_legacy(raw):
    raw = raw or {}
    return "layer" not in raw and isinstance(raw.get("deployment"), dict)


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


def record_dependencies(records, depends_on):
    """The recorded version of each lower layer, null where it has no record."""
    return {name: (records.get(name) or {}).get("version") for name in depends_on or []}


def check_layers(layer_class, check, every_class):
    """The planned layers a check judges: all for a check in every_class, else upgrade and downgrade runs."""
    return [layer for layer, run_class in layer_class.items() if check in every_class or run_class in UPGRADE_CLASSES]


def report_due(run_class):
    return run_class in UPGRADE_CLASSES


class FilterModule:
    def filters(self):
        return {
            "lift_layer_record": lift_layer_record,
            "claim_layer_dirs": claim_layer_dirs,
            "layer_fully_planned": layer_fully_planned,
            "layer_record_is_legacy": layer_record_is_legacy,
            "layer_release_version": layer_release_version,
            "classify_run": classify_run,
            "version_change_verdict": version_change_verdict,
            "upgrade_gate": upgrade_gate,
            "record_dependencies": record_dependencies,
            "check_layers": check_layers,
            "report_due": report_due,
        }
