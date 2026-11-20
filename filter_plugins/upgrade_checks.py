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

import os
import re

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

RECORD_FIELDS = ("layer", "version", "targetVersion", "status", "startedAt",
                 "completedAt", "forced", "forcedChecks", "failedComponent",
                 "haltReason", "dependencies", "installedBy")
UPGRADE_CLASSES = ("upgrade", "downgrade")
PLAIN_CLASSES = ("fresh", "reinstall", "resume")


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
            "resolve": "check the cluster connection and retry, or add --force to install as fresh",
            "always_gates": True}


def _upgrade_range(expr):
    """A bare version means that version or later. Only a string is a range: YAML reads an
    unquoted 1.10 as the float 1.1."""
    if not isinstance(expr, str):
        raise InvalidSpecifier(repr(expr))
    expr = expr.strip()
    if not expr:
        raise InvalidSpecifier("''")  # SpecifierSet("") would match every version
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


def upgrade_gate(verdicts, supports_upgrade, forced=False, layer_class=None):
    """Mark each verdict gating or not; return what refuses the run and what --force bypassed.

    supports_upgrade maps a layer name to its supports.upgrade declaration, layer_class a layer
    name to its run class: a plain install (fresh, reinstall, resume) is never refused, unless
    the verdict is marked always_gates.
    """
    layer_class = layer_class or {}
    checks = [dict(v, gating=bool(supports_upgrade.get(v["layer"])) and v["verdict"] == "blocking"
                   and (v.get("always_gates") or layer_class.get(v["layer"]) not in PLAIN_CLASSES))
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


def mark_always_gates(verdicts, before, layer, path):
    """The verdicts after a layer's own check file ran: what the file appended, marked
    always_gates, after the verdicts it found (before). Refused when the file changed those,
    or appended a verdict for another layer."""
    if verdicts[:len(before)] != before:
        raise ValueError("%s changed the verdicts before it; a check file may only append" % path)
    added = verdicts[len(before):]
    malformed = [v for v in added if not isinstance(v, dict) or not all(k in v for k in ("layer", "check", "verdict"))]
    if malformed:
        raise ValueError("%s appended a verdict without layer, check and verdict: %r" % (path, malformed[0]))
    others = sorted({str(v.get("layer")) for v in added if v.get("layer") != layer})
    if others:
        raise ValueError("%s (layer %s) appended verdicts for %s" % (path, layer, ", ".join(others)))
    return before + [dict(v, always_gates=True) for v in added]


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


# Compatibility: the release's requires against the layers underneath and the
# installer, and the release against the requires of the dependants installed.

INSTALLER = "installer"


def _range(expr, layer, name):
    try:
        spec = _upgrade_range(expr)
    except InvalidSpecifier:
        spec = None
    if spec is None or any(s.operator == "===" for s in spec):
        raise ValueError("layer %s: requires.%s %r is not a range" % (layer, name, expr))
    return spec


def _below(version, spec):
    """Whether version misses spec on a lower bound, rather than an upper one or an exclusion."""
    def under(s):
        if s.operator not in (">", ">=", "~=", "=="):
            return False
        bound = Version(s.version.rstrip(".*"))
        return version <= bound if s.operator == ">" else version < bound
    return any(not s.contains(version, prereleases=True) and under(s) for s in spec)


def versions_after_run(records, release, installer_version):
    """The version each layer has after this run: a planned layer its release, any other its
    record. The installer's own version is under installer."""
    versions = {name: (record or {}).get("version") for name, record in (records or {}).items()}
    versions.update(release or {})
    versions[INSTALLER] = installer_version
    return versions


def _forward_findings(layer, requires, layer_class, layer_records, versions, depends_on, install):
    """(blocking, advisory, resolve, subjects) for the release's requires and depends_on. subjects
    names, per resolve entry, the layer the fix installs or upgrades (bottom of the stack)."""
    blocking, advisory, resolve, subjects = [], [], [], []
    release = versions.get(layer)
    for dep in list(requires) + [d for d in depends_on or [] if d not in requires]:
        found = versions.get(dep)
        if not found:
            if dep in layer_class:
                why = "it declares no version.yaml"
            elif dep in layer_records:
                why = "its record has no version"
            else:
                why = "no deployment record"
            blocking.append("%s has no version (%s)" % (dep, why))
            resolve.append(install % dep)
            subjects.append(dep)
            continue
        if dep not in requires:
            continue
        spec, have = _range(requires[dep], layer, dep), _version(found, dep)
        if spec.contains(have, prereleases=True):
            continue
        found_in = blocking if _below(have, spec) else advisory
        found_in.append("%s %s requires %s %s, %s is %s" % (layer, release or "(unversioned)", dep, requires[dep],
                                                             dep, found))
        if dep == INSTALLER:
            resolve.append("use an installer release within %s" % requires[dep])
        else:
            resolve.append((install % dep) + " from a release within %s" % requires[dep])
        subjects.append(dep)
    return blocking, advisory, resolve, subjects


def compatibility_dependants(layer, layer_requires, layer_records, release, env, layer_class=None):
    """The installed layers (other than layer itself) whose requires on layer this release's
    version violates: one entry per dependant with the advisory text and its fix. layer_class
    excludes the layers this run already judges itself."""
    layer_class = layer_class or {}
    found = []
    for dependant, theirs in sorted((layer_requires or {}).items()):
        installed = (layer_records.get(dependant) or {}).get("version")
        if dependant in layer_class or not installed:
            continue
        if not isinstance(theirs or {}, dict):
            raise ValueError("layer %s: requires is not a mapping" % dependant)
        expr = (theirs or {}).get(layer)
        if expr is None:
            continue
        if not _range(expr, dependant, layer).contains(_version(release, layer), prereleases=True):
            # A record carries no requires, so the range is the dependant's checkout.
            found.append({
                "dependant": dependant,
                "reason": "%s (installed %s; range from its checkout) requires %s %s, %s is now %s"
                         % (dependant, installed, layer, expr, layer, release),
                "resolve": "install %s within %s, or upgrade %s first: ./es_auto_installer.sh init %s"
                          " --upgrade --env %s" % (layer, expr, dependant, dependant, env),
            })
    return found


def _bottom_up_resolve(resolve, subjects, layer_order):
    """De-duplicate resolve entries (first occurrence wins) and order the rest bottom-up by
    layer_order (infrastructure, platform, inference, then solution layers); unknown layers last,
    ties kept in their original order. A single fix stays plain text; more than one is numbered."""
    rank = {name: i for i, name in enumerate(layer_order or ())}
    seen, deduped = set(), []
    for text, subject in zip(resolve, subjects):
        if text in seen:
            continue
        seen.add(text)
        deduped.append((rank.get(subject, len(rank)), text))
    deduped.sort(key=lambda pair: pair[0])
    if not deduped:
        return None
    if len(deduped) == 1:
        return deduped[0][1]
    return " ".join("%d. %s" % (i + 1, text) for i, (_, text) in enumerate(deduped))


def compatibility_verdict(layer, layer_class, layer_requires, layer_records, versions, depends_on=(),
                          env="<env>", layer_order=()):
    """Judge one planned layer: blocking for a dependency below a lower bound of the release's
    requires, or without a version; advisory for one above an upper bound and for an installed
    dependant whose requires exclude the release."""
    layer_requires = layer_requires or {}
    requires = layer_requires.get(layer) or {}
    if not isinstance(requires, dict):
        raise ValueError("layer %s: requires is not a mapping" % layer)
    install = "./es_auto_installer.sh install %s --env " + env
    blocking, advisory, resolve, subjects = _forward_findings(layer, requires, layer_class, layer_records,
                                                               versions, depends_on, install)
    release = versions.get(layer)
    if release and layer_class.get(layer) != "reinstall":
        dependants = compatibility_dependants(layer, layer_requires, layer_records, release, env, layer_class)
        advisory += [d["reason"] for d in dependants]
        resolve += [d["resolve"] for d in dependants]
        subjects += [d["dependant"] for d in dependants]

    verdict = "blocking" if blocking else "advisory" if advisory else "pass"
    reason = "; ".join(blocking + advisory) or ("requires met" if requires else "nothing required")
    return {"layer": layer, "check": "compatibility", "verdict": verdict, "reason": reason,
            "resolve": _bottom_up_resolve(resolve, subjects, layer_order)}


# Configuration tracking: a layer lists the env files it owns and its rules in
# config_tracking.yaml, beside its components.yaml (docs/reference/adding_solutions.md).


CONFIG_TRACKING_FILE = "config_tracking.yaml"
_SEGMENT = re.compile(r"(?P<name>[^.\[\]]+)(?:\[(?P<field>[^=\]]+)=(?P<value>[^\]]*)\])?")
_MISSING = object()
_FACT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
_KEY = re.compile(r"^(?:[^:]+:)?{0}(?:\.{0})*$".format(r"[^.\[\]:]+(?:\[[^=\]:]+=[^\]:]*\])?"))


def config_tracking_strip(data, deny):
    """Drop every mapping key, at any depth, that matches one of the deny patterns, ignoring case."""
    patterns = [re.compile(p, re.IGNORECASE) for p in deny]

    def strip(node):
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if not any(p.search(str(k)) for p in patterns)}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node
    return strip(data)


def _load_tracking(path, deny):
    with open(path) as f:
        tracking = yaml.safe_load(f) or {}
    files, rules = tracking.get("files"), tracking.get("rules") or []
    if not isinstance(files, list) or not isinstance(rules, list):
        raise ValueError("%s: files must be a list, rules a list" % path)
    if not all(isinstance(name, str) for name in files):
        raise ValueError("%s: every entry of files must be a file name" % path)
    seen, facts = set(), {}
    for rule in rules:
        if not isinstance(rule, dict) or not rule.get("id") or not rule.get("key"):
            raise ValueError("%s: every rule needs an id and a key: %r" % (path, rule))
        if not _KEY.match(str(rule["key"])):
            raise ValueError("%s: %s: key %r is not <path> or <file>:<path>" % (path, rule["id"], rule["key"]))
        path_part = str(rule["key"]).partition(":")[2] or str(rule["key"])
        names = [n for m in _SEGMENT.finditer(path_part) for n in m.group("name", "field") if n]
        denied = [n for n in names if any(re.search(p, n, re.IGNORECASE) for p in deny)]
        if denied:
            raise ValueError("%s: %s: key %r names %s, which config_tracking_deny leaves out of the saved state,"
                             " so the rule would never fire" % (path, rule["id"], rule["key"], denied[0]))
        if rule["id"] in seen:
            raise ValueError("%s: rule id %s is used twice" % (path, rule["id"]))
        seen.add(rule["id"])
        sets = rule.get("sets") or {}
        if not isinstance(sets, dict):
            raise ValueError("%s: %s: sets must be a mapping" % (path, rule["id"]))
        for fact in sets:
            if not _FACT_NAME.match(str(fact)):
                raise ValueError("%s: %s: sets names %r: a fact name is lower case, digits and _,"
                                 " and does not start with _" % (path, rule["id"], fact))
            if fact in facts:
                raise ValueError("%s: rules %s and %s both set %s" % (path, facts[fact], rule["id"], fact))
            # A fact is set as text: a YAML false would become "False", which a when: reads as true.
            if not isinstance(sets[fact], str):
                raise ValueError("%s: %s: sets %s must be a quoted string, not %r"
                                 % (path, rule["id"], fact, sets[fact]))
            facts[fact] = rule["id"]
        if rule.get("verdict", "advisory") not in ("advisory", "blocking"):
            raise ValueError("%s: %s: verdict must be advisory or blocking" % (path, rule["id"]))
        if rule.get("when", "changed") not in ("changed", "set", "cleared"):
            raise ValueError("%s: %s: when must be changed, set or cleared" % (path, rule["id"]))
    return files, rules


def _tracking_file(layer_dir):
    path = os.path.join(layer_dir or "", CONFIG_TRACKING_FILE)
    return path if layer_dir and os.path.isfile(path) else None


def _capture(files, env_dir, deny):
    captured = {}
    for name in files:
        if os.path.isfile(os.path.join(env_dir, name)):
            with open(os.path.join(env_dir, name)) as f:
                content = yaml.safe_load(f) or {}
            if not isinstance(content, dict):
                raise ValueError("%s: a tracked file must hold a mapping" % os.path.join(env_dir, name))
            captured[name] = config_tracking_strip(content, deny)
    return captured


def config_tracking_capture(layer_dir, env_dir, deny):
    """The layer's tracked env files, {name: content without denied keys}; None without config_tracking.yaml."""
    path = _tracking_file(layer_dir)
    return _capture(_load_tracking(path, deny)[0], env_dir, deny) if path else None


def _resolve(config, key, files):
    """Value at <file>:<path> or <path>; a bare path is read from the first listed file holding its first segment."""
    file_name, sep, path = key.partition(":")
    if not sep:
        path, first = key, _SEGMENT.match(key).group("name")
        file_name = next((n for n in files if isinstance(config.get(n), dict) and first in config[n]), None)
    node = config.get(file_name, _MISSING)
    for match in _SEGMENT.finditer(path):
        if not isinstance(node, dict):
            return _MISSING
        node = node.get(match.group("name"), _MISSING)
        if match.group("field"):
            if not isinstance(node, list):
                return _MISSING
            node = next((e for e in node if isinstance(e, dict)
                         and str(e.get(match.group("field"))) == match.group("value")), _MISSING)
    return node


def _empty(value):
    return value is _MISSING or value in (None, "")


def _fires(when, old, new):
    if when == "set":
        return _empty(old) and not _empty(new)
    if when == "cleared":
        return not _empty(old) and _empty(new)
    return old is not _MISSING and new is not _MISSING and old != new


def _str(value):
    return "" if value in (_MISSING, None) else str(value)


def _text(template, old, new):
    """Substitute {old} and {new} in one pass, so a value holding the other placeholder stays as it is."""
    values = {"old": _str(old), "new": _str(new)}
    return re.sub(r"\{(old|new)\}", lambda m: values[m.group(1)], str(template))


def config_tracking_compare(current, saved, rules, files=()):
    """Compare a capture with the saved one and evaluate the rules.

    Returns {verdict: baseline|pass|advisory|blocking, changes: [<file>:<top-level key>],
    findings: [{id, key, verdict, old, new, message}], facts: {name: value}}.
    """
    if saved is None:
        return {"verdict": "baseline", "changes": [], "findings": [], "facts": {}}
    changes = sorted("%s:%s" % (name, key)
                     for name in set(current) | set(saved)
                     for key in set(current.get(name, {})) | set(saved.get(name, {}))
                     if current.get(name, {}).get(key, _MISSING) != saved.get(name, {}).get(key, _MISSING))
    files = list(files) or sorted(current)
    findings, facts = [], {}
    for rule in rules:
        old, new = _resolve(saved, rule["key"], files), _resolve(current, rule["key"], files)
        if not _fires(rule.get("when", "changed"), old, new):
            continue
        findings.append({"id": rule["id"], "key": rule["key"], "verdict": rule.get("verdict", "advisory"),
                         "old": _str(old), "new": _str(new),
                         "message": _text(rule.get("message", ""), old, new).strip()})
        facts.update({k: _text(v, old, new) for k, v in (rule.get("sets") or {}).items()})
    verdicts = {f["verdict"] for f in findings}
    verdict = "blocking" if "blocking" in verdicts else "advisory" if verdicts else "pass"
    return {"verdict": verdict, "changes": changes, "findings": findings, "facts": facts}


def config_tracking_verdict(layer_dir, env_dir, saved_raw, deny, layer, env="<env>", supports_upgrade=False):
    """{check: the config-tracking verdict of one layer, facts: what its fired rules set}.

    saved_raw is the config.yaml of Secret/<layer>-config-tracking, or None when there is none.
    A layer that declares supports.upgrade but ships no config_tracking.yaml is blocking, not skipped.
    """
    verdict = {"layer": layer, "check": "config-tracking", "verdict": "skipped",
               "reason": "no %s" % CONFIG_TRACKING_FILE, "resolve": None}
    path = _tracking_file(layer_dir)
    if not path:
        if supports_upgrade:
            verdict.update(verdict="blocking",
                           reason="%s declares supports.upgrade but ships no %s" % (layer, CONFIG_TRACKING_FILE),
                           resolve="add %s beside %s's components.yaml, or rerun with --force" % (CONFIG_TRACKING_FILE, layer))
        return {"check": verdict, "facts": {}}
    files, rules = _load_tracking(path, deny)
    current = _capture(files, env_dir, deny)
    saved = (yaml.safe_load(saved_raw) or {}).get("files", {}) if saved_raw else None
    result = config_tracking_compare(current, saved, rules, files)
    verdict["verdict"] = result["verdict"]
    if result["verdict"] == "baseline":
        verdict["reason"] = "no saved state; this run saves it"
    elif result["findings"]:
        verdict["reason"] = "; ".join("%s: %s" % (f["id"], f["message"] or "%s '%s' -> '%s'" % (f["key"], f["old"], f["new"]))
                                      for f in result["findings"])
    else:
        verdict["reason"] = ("changed, no rule: %s" % ", ".join(result["changes"])
                             if result["changes"] else "no tracked change")
    blocking = [f for f in result["findings"] if f["verdict"] == "blocking"]
    if blocking:
        verdict["resolve"] = "in env/%s set back %s" % (
            env, ", ".join("%s to '%s'" % (f["key"], f["old"]) for f in blocking))
    return {"check": verdict, "facts": result["facts"]}


def config_tracking_merge_facts(merged, layer, facts):
    """Add one layer's rule facts to {name: {layer, value}}; two layers setting one fact name is refused."""
    clash = sorted(n for n in facts if n in merged and merged[n]["layer"] != layer)
    if clash:
        raise ValueError("config_tracking.yaml of %s and %s both set %s"
                         % (merged[clash[0]]["layer"], layer, ", ".join(clash)))
    return dict(merged, **{n: {"layer": layer, "value": v} for n, v in facts.items()})


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
            "mark_always_gates": mark_always_gates,
            "check_layers": check_layers,
            "report_due": report_due,
            "versions_after_run": versions_after_run,
            "compatibility_verdict": compatibility_verdict,
            "config_tracking_capture": config_tracking_capture,
            "config_tracking_verdict": config_tracking_verdict,
            "config_tracking_merge_facts": config_tracking_merge_facts,
        }
