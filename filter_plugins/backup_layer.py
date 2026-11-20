# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""
Ansible filter plugin — the layer side of playbooks/backup.yaml's backup and restore.

Usage in roles/backup/tasks/layer.yaml and restore.yaml:
    {{ backup_layer | backup_profile_path(_layer_dirs) }}
    {{ backup_layer | restore_layer_selector(layer_names, label_key) }}
    {{ recorded_version | checkout_matches_record(checkout_version) }}
    {{ candidates | restore_backup_choice(version_label, recorded_version, velero_restore_from, bypass_label) }}
"""

from packaging.version import InvalidVersion, Version

PROFILE_FILE = "backup_profile.yaml"


def backup_profile_path(layer, layer_dirs):
    """The profile's fixed path, beside the components.yaml that declares the layer.

    Empty for a layer with no repository of its own under ext/ (the layers this
    repository declares): there is no place for its profile.
    """
    directory = (layer_dirs or {}).get(layer)
    return "%s/%s" % (directory, PROFILE_FILE) if directory else ""


def restore_layer_selector(layer, layer_names, label_key):
    """Restore label selector that leaves every other layer's objects alone.

    NotIn matches objects without the key too, so only objects labelled with
    another layer are left out. None when there is no other layer.
    """
    others = sorted(set(layer_names or []) - {layer})
    if not others:
        return None
    return {"matchExpressions": [{"key": label_key, "operator": "NotIn", "values": others}]}


def checkout_matches_record(record_version, checkout_version):
    """Whether the checkout is at the version the layer's record names (3.0 is 3.0.0).

    True when nothing is recorded: there is no installed release to differ from.
    """
    if not record_version:
        return True
    if not checkout_version:
        return False
    try:
        return Version(str(record_version).split("+")[0]) == Version(str(checkout_version).split("+")[0])
    except InvalidVersion:
        return str(record_version) == str(checkout_version)


def restore_backup_choice(candidates, version_label, recorded_version, override="", bypass_label=""):
    """The backup a restore should use.

    override wins outright. Otherwise the newest Completed backup (by
    completionTimestamp) labelled version_label with the layer's recorded_version
    (3.0 is 3.0.0, via checkout_matches_record) that does not carry bypass_label
    (a backup forced from a checkout at another version: restorable only by name).
    {"name": ...} on a choice, else {"error": "no record" | "no match", "available":
    ["<name> (<version>)", ...]} naming every Completed backup of the profile and
    the version it carries; "no match" adds "forced": [<name>, ...] when the
    recorded version has only such forced backups.
    """
    if override:
        return {"name": override}
    if not recorded_version:
        return {"error": "no record", "available": []}
    completed = [b for b in candidates or []
                 if (b.get("status") or {}).get("phase") == "Completed"
                 and (b.get("status") or {}).get("completionTimestamp")]
    available = ["%s (%s)" % (b["metadata"]["name"], (b.get("metadata") or {}).get("labels", {}).get(version_label) or "no version")
                 for b in completed]
    matching = [b for b in completed
                if checkout_matches_record(recorded_version, (b.get("metadata") or {}).get("labels", {}).get(version_label))]
    forced = [b for b in matching if bypass_label and bypass_label in ((b.get("metadata") or {}).get("labels") or {})]
    matching = [b for b in matching if b not in forced]
    if not matching:
        result = {"error": "no match", "available": available}
        if forced:
            result["forced"] = [b["metadata"]["name"] for b in forced]
        return result
    newest = sorted(matching, key=lambda b: b["status"]["completionTimestamp"], reverse=True)[0]
    return {"name": newest["metadata"]["name"]}


class FilterModule:
    def filters(self):
        return {
            "backup_profile_path": backup_profile_path,
            "restore_layer_selector": restore_layer_selector,
            "checkout_matches_record": checkout_matches_record,
            "restore_backup_choice": restore_backup_choice,
        }
