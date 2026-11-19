# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — the layer deployment record, the run class of an
install, and the gating of upgrade-check verdicts.

Used by roles/upgrade_checks and playbooks/includes/run_layer.yaml.

A record a layer still writes itself (Intel Chatbot Toolkit 3.0.0, layer
erag) nests its state under deployment: and has no top-level layer. It is lifted
to the top level on read; core writes over it only once the layer declares
supports.upgrade, the release that stops writing its own.
"""

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
    # The recorded version is parsed only for a complete record: on a halted or in-progress one,
    # upgrade_path_verdict parses it, so a version that does not parse is a verdict, not a preflight error.
    if status in ("in-progress", "halted"):
        target = record.get("targetVersion")
        if not target:
            return "conflict"
        try:
            return "resume" if _version(target, layer) == wanted else "conflict"
        except ValueError:
            # An unparseable targetVersion is a blocking verdict from version_change_verdict's
            # conflict branch (it only reports the raw string), not a preflight crash.
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


def upgrade_path_verdict(version_data, layer, record, run_class, env="<env>"):
    """Whether the recorded version lies in the release's upgrade_base_version range.

    A conflict (a run towards another release, halted or in progress) is judged from the
    recorded version too, so a forced run knows whether this release supports that path.
    classify_run parses the recorded version of an upgrade or a downgrade, not of a conflict;
    a conflict's version that does not parse is a blocking verdict here."""
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
    release = layer_release_version(version_data)
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
            "upgrade_gate": upgrade_gate,
            "record_forced": record_forced,
            "record_dependencies": record_dependencies,
            "record_version": record_version,
            "check_layers": check_layers,
            "report_due": report_due,
        }
