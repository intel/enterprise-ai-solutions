#!/usr/bin/env bash
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Former name of toolkit_manager.sh, kept so existing scripts, CI and docs keep working.
_tm_installer="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/toolkit_manager.sh"
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    # shellcheck source=toolkit_manager.sh
    source "$_tm_installer" "$@"
    return
fi
echo "Note: es_auto_installer.sh is now toolkit_manager.sh; the old name still works." >&2
exec "$_tm_installer" "$@"
