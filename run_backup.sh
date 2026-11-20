#!/usr/bin/env bash
# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# run_backup.sh — back up, restore or list one layer's backups with Velero.
# A thin front end to playbooks/backup.yaml: it assembles the same playbook
# arguments toolkit_manager.sh does (env configs in record order, kubeconfig),
# from the installer's own functions, so the two cannot drift.
set -euo pipefail

# shellcheck source=toolkit_manager.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/toolkit_manager.sh"

backup_usage() {
    local me; me=$(basename "$0")
    cat <<EOF

  Usage:  $me <action> <layer> [options] [-- ansible-playbook args]

  Actions:
    backup  <layer>        take a Velero backup of the layer
    restore <layer>        restore the layer from a backup (asks first)
    list    <layer>        list the layer's backups

  Options:
    --env <name>           env directory under env/<name>/ (default: local)
    --from <backup>        (restore) restore this backup instead of the automatic pick,
                           the newest backup of the recorded version not taken with
                           --force (list marks it)
    --force                (restore) skip the confirmation prompt (required where
                           there is no terminal to answer it); (backup) take the
                           backup although the checkout is at another version than
                           the installed one, labelled so a restore never picks it
                           without --from and it never satisfies backup-freshness
    -h, --help             this help
    -- <args...>           pass remaining args verbatim to ansible-playbook

  Environment:
    TKM_LOG_LEVEL          info (default) | debug | trace, as for toolkit_manager.sh

  Examples:
    $me backup erag --env prod
    $me list erag --env prod
    $me restore erag --env prod
    $me restore erag --env prod --from <backup> --force

EOF
}

# backup_parse_args — the installer's globals (ACTION TARGET ENV_NAME FORCE ...) set the
# way parse_args sets them, plus FROM.
backup_parse_args() {
    ACTION="" TARGET="" ENV_NAME="local" ONLY=false FORCE=false SKIP="" FROM=""
    EXTRA_VARS=()

    [[ $# -eq 0 || "${1:-}" =~ ^(-h|--help)$ ]] && { backup_usage; exit 0; }
    ACTION="$1"; shift
    [[ "$ACTION" =~ ^(backup|restore|list)$ ]] \
        || err "Unknown action: '${ACTION}'. Valid: backup restore list (try --help)"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --env)   _opt_value --env "${2:-}"; ENV_NAME="$2"; shift 2 ;;
            --from)  [[ "$ACTION" == "restore" ]] || err "--from applies to restore only."
                     _opt_value --from "${2:-}"; FROM="$2"; shift 2 ;;
            --force) [[ "$ACTION" != "list" ]] || err "--force applies to backup and restore only."
                     FORCE=true; shift ;;
            --)      shift; EXTRA_VARS+=("$@"); break ;;
            -*)      err "Unknown option: '$1'. Valid: --env <name> | --from <backup> | --force (pass ansible args after --). Try --help." ;;
            *)       [[ -z "$TARGET" ]] || err "Unexpected argument: '$1'. ${ACTION} takes one layer."
                     TARGET="$1"; shift ;;
        esac
    done
    [[ -n "$TARGET" ]] || err "${ACTION} needs a layer. Layers: $(known_layers | tr '\n' ' ')"
}

backup_main() {
    backup_parse_args "$@"

    ensure_env_dir "$ENV_NAME"
    ensure_repos "$TARGET"
    _layer_exists "$TARGET" \
        || err "Unknown layer: '${TARGET}'. Layers: $(known_layers | tr '\n' ' ')"
    if [[ -n "$(layer_manifest "$TARGET")" ]] \
       && ! grep -qxF -- "$TARGET" <<< "$(solution_layers)"; then
        err "env/${ENV_NAME} was not provisioned for layer '${TARGET}'. Run:  ${INSTALLER_CMD} init ${TARGET} --env ${ENV_NAME}"
    fi
    ensure_deps
    resolve_inventory

    LOG="${ENV_LOG_DIR}/backup-${TARGET}-$(date +%Y%m%d-%H%M%S).log"

    local -a vars
    playbook_vars
    vars+=(-e "backup_action=${ACTION}")
    [[ -n "$FROM" ]] && vars+=(-e "velero_restore_from=${FROM}")
    [[ "$ACTION" == "backup" && "$FORCE" == "true" ]] && vars+=(-e "backup_force=true")

    if [[ "$ACTION" == "restore" ]]; then
        confirm "About to restore '${TARGET}' in env/${ENV_NAME} from ${FROM:-the newest backup of its recorded version}.
  This DELETES the Helm releases and PersistentVolumeClaims its backup profile lists first,
  then replays the backup over them. Objects in the profile's namespaces that do not carry
  the toolkit-manager.intel.com/layer label are replaced too; a profile can exclude customer-owned
  kinds from that with exclude_resources."
        vars+=(-e "backup_confirm=yes")
    fi

    info "${ACTION^}: ${TARGET}  (env=${ENV_NAME}, log ${LOG})"
    run backup "${vars[@]}" "${EXTRA_VARS[@]}"
}

backup_main "$@"
