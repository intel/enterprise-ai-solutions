#!/usr/bin/env bash
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Requires bash >= 4.3: mapfile, associative arrays, ${var^^}.
set -euo pipefail

# Force a UTF-8 locale for the whole run.
export LANG="${LANG:-C.UTF-8}" LC_ALL="${LC_ALL:-C.UTF-8}"

# ES_LOG_LEVEL: info (default) = err/warn/info only; debug = + grey logs, tool
# download progress, ansible -vvv; trace = + bash xtrace and ANSIBLE_DEBUG.
ES_LOG_LEVEL="${ES_LOG_LEVEL:-info}"
case "$ES_LOG_LEVEL" in
    info)  _LOG_LEVEL=0 ;;
    debug) _LOG_LEVEL=1 ;;
    trace) _LOG_LEVEL=2 ;;
    *) echo "ES_LOG_LEVEL must be info, debug, or trace (got: $ES_LOG_LEVEL)" >&2; exit 1 ;;
esac

if (( _LOG_LEVEL >= 2 )); then
    set -x
    export ANSIBLE_DEBUG=true
fi

# =============================================================================
# Constants
# =============================================================================
# Paths are deliberately not readonly: the test suite sources this file and
# repoints them at a fixture tree.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly INSTALLER_VERSION="$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null || echo dev)"

CONFIG_DIR="${SCRIPT_DIR}/configs"
COMPONENTS="${CONFIG_DIR}/components.yaml"
DEFAULTS_DIR="${CONFIG_DIR}/defaults"
# Layer manifests: configs/repos/repos.<layer>.yaml — filename is the layer name.
REPOS_DIR="${CONFIG_DIR}/repos"
REPOS_GLOB="${REPOS_DIR}/repos.*.yaml"

EXT_DIR="${SCRIPT_DIR}/ext"
VENV="${SCRIPT_DIR}/.venv"
ENV_ROOT="${SCRIPT_DIR}/env"
# Per-env provisioning record, written by init. See docs/adding_solutions.md.
readonly SOLUTIONS_BASE=".solutions.yaml"
# Bumped whenever a recorded field changes name or meaning, so an older record is
# refused with the re-init to run instead of being misread as empty.
readonly SOLUTIONS_SCHEMA=2

readonly YQ_VERSION="v4.53.2"
readonly KUBECTL_VERSION="v1.34.3"
readonly HELM_VERSION="v3.20.2"

# Valid CLI actions.
readonly -a ACTIONS=(configure show init install teardown validate status add-node remove-node)

# =============================================================================
# Mutable globals — every one of these, and nothing else, is written after startup
# =============================================================================
#   parse_args        ACTION TARGET ENV_NAME ONLY EXTRA_VARS DRY_RUN
#                     INIT_LAYER INIT_FLAVOUR INIT_UPGRADE
#   ensure_env_dir    ENV_DIR ENV_LOG_DIR ENV_INVENTORY_DIR GLOBAL_CONFIG
#   resolve_inventory INVENTORY
#   ensure_sudo       _NEED_BECOME_PASS
#   _discover_layers  LAYERS
#   main              LOG
#   _resolve_kubeconfig  KUBE_CFG IS_BYO
#   ensure_repos      ANSIBLE_ROLES_PATH (exported)
# init sets ENV_DIR itself: it runs before any env exists, so ensure_env_dir cannot.

# =============================================================================
# Output
# =============================================================================
# err/warn go to stderr, info/ok to stdout. Two traps worth knowing:
#   - err exits, so `[[ cond ]] || err "..."` is safe as a function's last statement.
#   - err inside $(...) or < <(...) exits only that subshell and is swallowed. Assign
#     to a variable first, or wrap the callee in ( ) and handle the failure.
RED=$'\033[0;31m' GRN=$'\033[0;32m' YEL=$'\033[0;33m' DIM=$'\033[2m' RST=$'\033[0m'

err()  { echo -e "${RED}ERROR: $*${RST}" >&2; exit 1; }
warn() { echo -e "${YEL}WARN: $*${RST}" >&2; }
info() { printf '%s\n' "$*"; }
infon() { printf '%s' "$*"; }
ok()   { echo -e "${GRN}$*${RST}"; }
# Box banner for the final milestone. Each arg is one line; the box widens to
# the longest. Padding uses ${#l}, so keep the content ASCII — a multibyte glyph
# (em dash, emoji) counts as >1 under LC_ALL=C and skews the right edge.
banner() {
    local -a lines=("$@")
    local w=0 l pad bar="" i
    for l in "${lines[@]}"; do (( ${#l} > w )) && w=${#l}; done
    for (( i = 0; i < w + 2; i++ )); do bar+='─'; done
    printf '\n%s╭%s╮%s\n' "$GRN" "$bar" "$RST"
    for l in "${lines[@]}"; do
        pad=$(( w - ${#l} ))
        printf '%s│%s %s%*s %s│%s\n' "$GRN" "$RST" "$l" "$pad" "" "$GRN" "$RST"
    done
    printf '%s╰%s╯%s\n\n' "$GRN" "$bar" "$RST"
}
_quiet() {
    if (( _LOG_LEVEL >= 1 )); then
        printf '\n'
        "$@" 2>&1 | while IFS= read -r _line; do echo -e "${DIM}  ${_line}${RST}" >&2; done
        return "${PIPESTATUS[0]}"
    else
        # Captured, not discarded: on failure the reason is the only useful thing here.
        local _log; _log=$(mktemp)
        "$@" &>"$_log" &
        local _pid=$!
        while kill -0 "$_pid" 2>/dev/null; do
            sleep 2
            kill -0 "$_pid" 2>/dev/null && printf '.'
        done
        local _rc=0; wait "$_pid" || _rc=$?
        printf '\n'
        (( _rc == 0 )) || { warn "failed (exit ${_rc}), last 15 lines:"; tail -15 "$_log" >&2
                            info "  full output: re-run with ES_LOG_LEVEL=debug"; }
        rm -f "$_log"
        return "$_rc"
    fi
}

# =============================================================================
# Usage
# =============================================================================
# confirm <prompt> — y/N gate before destructive work. --force skips it; a non-interactive
# run without it is refused rather than proceeding on a prompt nobody can answer.
confirm() {
    [[ "${FORCE:-false}" == "true" ]] && return 0
    [[ -t 0 ]] || err "$1
  Refusing: stdin is not a terminal and --force was not passed."
    local reply=""
    read -rp "$1 Proceed? [y/N] " reply || true
    [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]] || err "Aborted."
}

usage() {
    local me; me=$(basename "$0")
    cat <<EOF

  Usage:  $me <action> [args] [-- ansible-playbook args]

  Actions:
    configure              install Python ≥ 3.11 + venv pkg + yq + kubectl + helm
                           (sudo, PERMANENT system changes). Run once per machine;
                           skip if those are already present.
    init <layer>           create env and seed configs. Clones every repo in
                           configs/repos/repos.<layer>.yaml at its pinned rev
                           and seeds a config.<layer>.yaml for each. --flavour
                           selects a pipeline preset (from pipelines/). Records
                           what it provisioned in env/<env>/.solutions.yaml.
    show                   print available layers/components
    install   <target>     provision a layer or component into env/<env>/
    teardown  <target>     remove a layer or component
    validate  <target>     run validate.yaml against current state
    status                 show what is currently installed (namespaces, pods,
                           helm releases, endpoints)
    add-node               scale worker node(s) into an already-provisioned cluster
                           (Kubespray scale.yml). Edit env/<env>/nodes.yaml first
                           (add worker entries) — new nodes are auto-detected by
                           diffing the rendered inventory against the live cluster;
                           no node name is passed. Control-plane node additions are
                           refused — use 'install --target kubernetes' for those.
    remove-node            remove node(s) from an already-provisioned cluster
                           (Kubespray remove-node.yml). Edit env/<env>/nodes.yaml
                           first (delete entries) — removed nodes are auto-detected
                           the same way. The current first control-plane/etcd node
                           cannot be removed this way (Kubespray limitation).

  Targets:
    <layer>                single layer (Ansible routes it to its components)
    <component>            single component (Ansible routes it to its layer)

  Options:
    --env <name>           env directory under env/<name>/ (default: local)
    --flavour <name>       (init) pipeline preset for the layer being inited
    --upgrade              (init) move already-cloned ext/ repos onto the revs the
                           manifests pin now. Refuses on local changes; never
                           rewrites your configs, only reports what changed.
    --only                 skip dep auto-pull; run target alone
    --force                skip the confirmation prompt (required in CI, where there
                           is no terminal to answer it)
    --skip <names>         comma-separated layers or components to leave out of the
                           plan, e.g. --skip erag to tear down the cluster without
                           uninstalling it first
    --dry-run              (add-node/remove-node) print the computed node diff and
                           the Kubespray command that would run, then exit
    -h, --help             this help
    -v, --version          print version
    -- <args...>           pass remaining args verbatim to ansible-playbook

  Environment:
    ES_LOG_LEVEL           info (default) | debug (verbose logs + ansible -vvv)
                           | trace (+ bash xtrace + ANSIBLE_DEBUG)

  Inventory:
    The env's inventory (env/<env>/inventory/hosts.yaml) is in kubespray-
    compatible YAML format. Required groups: kube_control_plane, kube_node,
    etcd. Additional groups (nfs_server, storage_nodes) are optional.

  Examples:
    $me show                                   # list layers/components
    $me configure                              # one-time machine prep
    $me init inference                         # seed env/local/ for inference
    $me init erag                              # seed env/local/ for erag (+ deps)
    $me init erag --flavour docsum             # erag + docsum pipeline preset
    $me init erag --env prod                   # seed env/prod/ for erag
    $me init erag --upgrade                    # move ext/ to the revs pinned now
    $me install erag                           # install just the erag layer
    $me status                                 # see what's installed (env=local)
    $me install --env prod platform            # multi-env on one bastion
    $me install metallb --only -- -vvv
    $me install velero --only                  # backup mechanism (opt-in)
    $me add-node --dry-run                     # preview after editing nodes.yaml
    $me add-node                                # scale in new worker(s) from nodes.yaml
    $me remove-node                             # remove node(s) dropped from nodes.yaml

EOF
}

# =============================================================================
# Registry & layer manifests
# =============================================================================
# The merged components.yaml (this repo + every cloned ext repo) is the registry:
# it decides which components exist. The per-layer manifests decide what to clone.

# A leading underscore marks a helper used only inside its own section; anything
# without one is called across sections or dispatched from main.
# Print the merged components.yaml from ai-solutions + every ext repo
_merged_components() {
    local files=("$COMPONENTS")
    [[ -d "$EXT_DIR" ]] && while IFS= read -r -d '' f; do files+=("$f"); done < \
        <(find -L "$EXT_DIR" -name components.yaml -type f \
                -not -path '*/\.git/*' -print0 2>/dev/null | sort -z)
    # Surface a malformed ext components.yaml instead of emitting an empty table.
    yq eval-all '. as $i ireduce ({}; . *+ $i)' "${files[@]}" \
        || warn "failed to merge components.yaml (malformed ext file? files: ${files[*]})"
}

# _discover_layers — populate LAYERS from the MERGED components (ai-solutions + ext), not
# ai-solutions alone, so an ext-only layer still renders. Lazy: called after yq exists.
LAYERS=()
_discover_layers() {
    mapfile -t LAYERS < <(_merged_components | yq -r '.layers[].name' 2>/dev/null)
}

# _require_yq — mikefarah yq v4+. A distro package under that name is often v3 or the
# unrelated Python yq, and neither understands a single expression in this file; without
# this the symptom is "Unknown layer" rather than "your yq is the wrong one".
_require_yq() {
    command -v yq &>/dev/null \
        || err "yq not found — run './es_auto_installer.sh configure' or install yq ${YQ_VERSION}."
    local major; major=$(yq --version 2>&1 | grep -oE '[0-9]+' | head -1)
    (( ${major:-0} >= 4 )) \
        || err "the yq on PATH reports major version ${major:-?}; this needs mikefarah yq >= ${YQ_VERSION}."
}

show_table() {
    _require_yq

    _discover_layers
    local merged; merged=$(_merged_components)
    local -A by_layer=()
    local -A layer_enabled=()
    local layer name enabled

    while IFS=$'\t' read -r layer name; do
        [[ -z "$layer" || -z "$name" ]] && continue
        by_layer[$layer]+="$name"$'\n'
    done < <(echo "$merged" | yq -r '.components[] | [.layer, .name] | @tsv' 2>/dev/null)

    while IFS=$'\t' read -r layer enabled; do
        [[ -z "$layer" ]] && continue
        layer_enabled[$layer]="$enabled"
    done < <(echo "$merged" | yq -r '.layers[] | [.name, (.enabled | tostring)] | @tsv' 2>/dev/null)

    local l label
    for l in "${LAYERS[@]}"; do
        [[ -z "${by_layer[$l]:-}" ]] && continue
        label="${l^^}"
        [[ "${layer_enabled[$l]:-true}" == "false" ]] && label="${label} ${DIM}(opt-in: install ${l})${RST}"
        # DIM/RST are real ESC bytes ($'\033...'), so %s prints them verbatim — do
        # not "fix" this to %b (that would re-interpret backslashes in layer names).
        printf '\n%s\n' "$label"
        while IFS= read -r name; do
            [[ -n "$name" ]] && printf '  - %s\n' "$name"
        done <<< "${by_layer[$l]}"
    done
    echo
}

# =============================================================================
# Host tooling — python, venv, yq, kubectl, helm
# =============================================================================
# Python helpers — check for suitable Python, print hints if missing.
_find_python() {
    local py minor
    for py in python3.12 python3.11 python3; do
        command -v "$py" &>/dev/null || continue
        minor=$("$py" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
        (( minor >= 11 )) && { echo "$py"; return 0; }
    done
    return 1
}

_python_hint() {
    cat >&2 <<EOF

Python ≥ 3.11 is required.

  Auto-install:           ./es_auto_installer.sh configure
  Manual Ubuntu 22.04:    sudo add-apt-repository ppa:deadsnakes/ppa
                          sudo apt install python3.11 python3.11-venv python3.11-dev
  Manual Ubuntu 24.04:    sudo apt install python3 python3-venv python3-dev
  Manual RHEL/Rocky:      sudo dnf install python3.11 python3.11-pip

EOF
}

_venv_hint() {
    local pyver="$1"
    cat >&2 <<EOF

python${pyver}-venv is not available (cannot 'import ensurepip').

  Auto-install:  ./es_auto_installer.sh configure
  Manual Debian: sudo apt install python${pyver}-venv
  RHEL/Rocky:    venv ships with base python — re-check Python install

EOF
}

configure() {
    warn "configure: this WILL make permanent changes to your system"
    cat >&2 <<EOF

    This command will, using sudo:
    - install Python ≥ 3.11 (deadsnakes PPA on Ubuntu 22.04, base pkgs elsewhere)
    - install the matching python<ver>-venv package
    - install a UTF-8 locale (en_US.UTF-8)
    - install yq, kubectl, and helm into /usr/local/bin
    - create the installer's Python venv under .venv/

EOF
    confirm "    configure makes the PERMANENT system changes listed above."

    # configure needs root. If not passwordless, prompt once here so
    # _quiet-backgrounded sudo commands use the cached credential.
    if [[ $EUID -ne 0 ]]; then
        command -v sudo &>/dev/null \
            || err "configure needs root: 'sudo' is not installed and you are not root."
        sudo -n true &>/dev/null || sudo -v || err "sudo authentication failed."
    fi

    case "$(_detect_os)" in
        debian) _configure_debian ;;
        rhel)   _configure_rhel ;;
        *)      err "Unsupported OS. Install Python ≥ 3.11, the matching venv pkg, yq, kubectl, and helm manually." ;;
    esac
    _configure_locale
    _install_yq
    _install_kubectl
    _install_helm
    _ensure_venv
    ok "configure: done."
    info "Next:  ./es_auto_installer.sh init <layer>  (see README.md for details)"
}

_detect_os() {
    [[ -f /etc/os-release ]] || { echo unknown; return; }
    . /etc/os-release
    case "$ID" in
        ubuntu|debian)                       echo debian ;;
        rhel|rocky|centos|almalinux|fedora)  echo rhel ;;
        *)                                   echo unknown ;;
    esac
}

_configure_debian() {
    . /etc/os-release
    export DEBIAN_FRONTEND=noninteractive
    infon "Updating package index"
    _quiet sudo apt-get update -qq
    case "${VERSION_ID:-}" in
        22.04)
            infon "Installing Python 3.11 via deadsnakes (Ubuntu 22.04)"
            _quiet bash -c 'sudo apt-get install -y -qq software-properties-common && sudo add-apt-repository -y ppa:deadsnakes/ppa && sudo apt-get update -qq && sudo apt-get install -y -qq python3.11 python3.11-venv python3.11-dev'
            ;;
        24.04)
            infon "Installing Python 3 (Ubuntu 24.04)"
            _quiet sudo apt-get install -y -qq python3 python3-venv python3-dev
            ;;
        *) err "Unsupported Ubuntu version: ${VERSION_ID:-?} (supported: 22.04, 24.04)" ;;
    esac
}

_configure_rhel() {
    infon "Installing Python 3.11 (RHEL/Rocky)"
    _quiet sudo dnf install -y python3.11 python3.11-pip
}

# Provision en_US.UTF-8 for interactive shells; best-effort (the runtime
# C.UTF-8 export already guarantees correctness, so failure is non-fatal).
_configure_locale() {
    infon "Configuring UTF-8 locale"
    case "$(_detect_os)" in
        debian)
            _quiet bash -c 'sudo apt-get install -y -qq locales && sudo locale-gen en_US.UTF-8 && sudo update-locale LANG=en_US.UTF-8' \
                || { warn "Could not provision en_US.UTF-8 — continuing with ${LANG}."; return 0; }
            ;;
        rhel)
            _quiet sudo dnf install -y glibc-langpack-en \
                || { warn "Could not install glibc-langpack-en — continuing with ${LANG}."; return 0; }
            ;;
    esac
}

# _arch — map `uname -m` to release-artifact arch names (amd64/arm64).
_arch() {
    case "$(uname -m)" in
        x86_64|amd64)  echo amd64 ;;
        aarch64|arm64) echo arm64 ;;
        *) err "Unsupported CPU architecture: $(uname -m) (supported: x86_64, aarch64)" ;;
    esac
}

# _download <url> <dest> [--sudo] — fetch to dest via curl (fallback wget) with
# bounded connect/transfer time + 3 retries, so a black-holed connection fails
# fast instead of hanging. --sudo writes dest as root, forwarding the caller's
# proxy vars (sudo scrubs env otherwise). Quiet unless ES_LOG_LEVEL>=debug.
_download() {
    local url="$1" dest="$2" use_sudo="${3:-}"
    local -a pre=()
    [[ "$use_sudo" == "--sudo" ]] && pre=(sudo --preserve-env=http_proxy,https_proxy,no_proxy)

    # curl first: RHEL minimal images ship curl and no wget.
    if command -v curl &>/dev/null; then
        local -a q=(-sS); (( _LOG_LEVEL >= 1 )) && q=(--progress-bar)
        "${pre[@]}" curl -fL "${q[@]}" --connect-timeout 15 --max-time 300 --retry 3 \
            -o "$dest" "$url" && return 0
    elif command -v wget &>/dev/null; then
        local -a q=(-q); (( _LOG_LEVEL >= 1 )) && q=(--progress=bar:force:noscroll)
        "${pre[@]}" wget "${q[@]}" --connect-timeout=15 --read-timeout=60 --tries=3 \
            -O "$dest" "$url" && return 0
    else
        err "downloads need curl or wget; neither is installed."
    fi

    err "Download failed or timed out: ${url}
  Behind a proxy? Export http_proxy/https_proxy and re-run — they are forwarded
  through sudo automatically, if not double check connectivity and firewall rules."
}

_install_yq() {
    if command -v yq &>/dev/null; then warn "yq exists ($(yq --version 2>&1 | grep -oP 'v[\d.]+' || echo '?')) — ensure ≥ ${YQ_VERSION}"; return 0; fi
    local arch; arch=$(_arch)
    infon "Installing yq ${YQ_VERSION} (${arch})"
    local tmp; tmp=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN
    _quiet _download \
        "https://github.com/mikefarah/yq/releases/download/${YQ_VERSION}/yq_linux_${arch}" \
        "$tmp/yq"
    sudo install -m 0755 "$tmp/yq" /usr/local/bin/yq \
        || err "yq install failed: could not install binary into /usr/local/bin."
    hash -r
    yq --version &>/dev/null \
        || err "yq install failed: /usr/local/bin/yq is not runnable (downloaded yq_linux_${arch} — check network/arch)."
}

_install_kubectl() {
    if command -v kubectl &>/dev/null; then warn "kubectl exists ($(kubectl version --client -o json 2>/dev/null | yq -r '.clientVersion.gitVersion' 2>/dev/null || echo '?')) — ensure ≥ ${KUBECTL_VERSION}"; return 0; fi
    local arch; arch=$(_arch)
    infon "Installing kubectl ${KUBECTL_VERSION} (${arch})"
    local tmp; tmp=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN
    _quiet _download \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${arch}/kubectl" \
        "$tmp/kubectl"
    sudo install -m 0755 "$tmp/kubectl" /usr/local/bin/kubectl \
        || err "kubectl install failed: could not install binary into /usr/local/bin."
    hash -r
    kubectl version --client &>/dev/null \
        || err "kubectl install failed: /usr/local/bin/kubectl is not runnable (downloaded linux/${arch} — check network/arch)."
}

_install_helm() {
    if command -v helm &>/dev/null; then warn "helm exists ($(helm version --short 2>/dev/null || echo '?')) — ensure ≥ ${HELM_VERSION}"; return 0; fi
    local arch; arch=$(_arch)
    infon "Installing helm ${HELM_VERSION} (${arch})"
    local tmp; tmp=$(mktemp -d)
    # shellcheck disable=SC2064
    trap "rm -rf '$tmp'" RETURN
    _quiet _download \
        "https://get.helm.sh/helm-${HELM_VERSION}-linux-${arch}.tar.gz" \
        "$tmp/helm.tar.gz"
    tar -xzf "$tmp/helm.tar.gz" -C "$tmp" \
        || err "helm install failed: could not unpack tarball (corrupt download?)."
    sudo install -m 0755 "$tmp/linux-${arch}/helm" /usr/local/bin/helm \
        || err "helm install failed: could not install binary into /usr/local/bin."
    hash -r
    helm version &>/dev/null \
        || err "helm install failed: /usr/local/bin/helm is not runnable (check network/arch)."
}

# layer_manifest <layer> — path to a layer's manifest, or empty if none exists.
layer_manifest() {
    local f="${REPOS_DIR}/repos.${1}.yaml"
    [[ -f "$f" ]] && printf '%s' "$f"
    return 0   # empty output, not failure — callers test the string under set -e.
}

# _registry_layers — layers declared in the merged components.yaml (ai-solutions + cloned ext).
_registry_layers() {
    _merged_components | yq -r '.layers[].name' 2>/dev/null
}

# known_layers — every nameable layer, sorted. A layer is nameable when a manifest
# declares the repos it needs OR the merged registry declares the layer itself: ai-solutions'
# layers ship every role in this repo, so they have no manifest and need none.
known_layers() {
    local f base
    { for f in $REPOS_GLOB; do
          [[ -e "$f" ]] || continue
          base=$(basename "$f")            # repos.<layer>.yaml
          base="${base#repos.}"
          printf '%s\n' "${base%.yaml}"
      done
      _registry_layers
    } | sort -u
}

# _layer_exists <layer> — manifest-backed or registry-declared.
#
# Here-string, not a pipe: `grep -q` closes the pipe on its first match, the producer dies
# of SIGPIPE, and pipefail then reports the whole lookup as failed. Timing-dependent, so it
# presents as a layer that exists "sometimes".
_layer_exists() {
    [[ -n "$(layer_manifest "$1")" ]] && return 0
    grep -qxF -- "$1" <<< "$(_registry_layers)"
}

# require_layer <layer> — validate the layer and echo its manifest path, which is
# empty for an ai-solutions layer that needs no external repos.
require_layer() {
    _layer_exists "$1" \
        || err "Unknown layer: '${1}'. Valid: $(known_layers | tr '\n' ' ')(a solution layer also needs a configs/repos/repos.<layer>.yaml)."
    local mf; mf=$(layer_manifest "$1")
    # Parsed here because callers read manifest_rows through a process substitution, where
    # a yq failure exits only the subshell and is indistinguishable from "no repos".
    [[ -z "$mf" ]] || yq -e '.repos | tag == "!!seq"' "$mf" >/dev/null 2>&1 \
        || err "${mf#${SCRIPT_DIR}/} does not parse, or has no 'repos:' list. Check it with:  yq . ${mf#${SCRIPT_DIR}/}"
    printf '%s' "$mf"
}

# manifest_rows <manifest> — emit one pipe-joined row per repo, in file order.
manifest_rows() {
    [[ -n "${1:-}" && -f "$1" ]] || return 0   # layer with no manifest → no repos
    yq -r '.repos[] | [(.layer // ""), (.url // ""), (.dest // ""), (.rev // "main"),
                       (.deployment_subdir // ""), (.config_dir // ""),
                       (.default_flavour // ""), (.model_catalog // "")] | join("|")' \
        "$1" 2>/dev/null
}

# =============================================================================
# Provisioning record — env/<env>/.solutions.yaml
# =============================================================================
# Written by init: which layers this env was provisioned for, which
# config.<layer>.yaml each one seeded, and the git rev init asked for.
#
# `rev` is intent, never a resolved commit — developers switch revs in ext/ by hand,
# so a record compared against HEAD would nag constantly. It is persisted because an
# upgrade replaces this checkout's manifests, after which nothing else remembers what
# the previous version pinned.
# =============================================================================

solutions_file() { printf '%s' "${ENV_ROOT}/${1}/${SOLUTIONS_BASE}"; }

_solutions_create() {
    local f="$1"
    [[ -f "$f" ]] && return 0
    printf '%s\n' \
        "# Generated by es_auto_installer.sh init — do not hand-edit." \
        "# What this env was provisioned for. 'rev' is what init asked for, not what is" \
        "# checked out now (ask git for that)." \
        "schema: ${SOLUTIONS_SCHEMA}" "inited: []" "layers: []" > "$f"
}

# _solutions_upsert <file> — replace the entry for SL_NAME in place, or append.
# In place, because entry order is config precedence: manifests list dependencies
# first, so a layer's own config must stay after the ones it builds on.
_solutions_upsert() {
    local f="$1"
    local entry='{
        "name": strenv(SL_NAME), "config": strenv(SL_CONFIG),
        "seeded_from": strenv(SL_SEEDED_FROM), "repo": strenv(SL_REPO),
        "url": strenv(SL_URL), "rev": strenv(SL_REV),
        "model_catalog": strenv(SL_MC), "seeded_by": strenv(SL_BY)
    }'
    if yq -e '.layers[] | select(.name == strenv(SL_NAME))' "$f" >/dev/null 2>&1; then
        yq -i "(.layers[] | select(.name == strenv(SL_NAME))) = ${entry}" "$f"
    else
        yq -i ".layers += [${entry}]" "$f"
    fi
}

# _solutions_get <file> <layer> <key> — one recorded field, empty if unrecorded.
_solutions_get() {
    [[ -f "$1" ]] || return 0
    SL_NAME="$2" SL_KEY="$3" \
        yq -r '.layers[] | select(.name == strenv(SL_NAME)) | .[strenv(SL_KEY)] // ""' \
        "$1" 2>/dev/null || true
}

# solution_configs — config.<layer>.yaml for every layer this env was provisioned
# with, in record order, as absolute paths.
#
# Not target-scoped: teardown resolves upward, so a plan for any layer can contain
# the layers above it, and their config must be loaded for the `enabled:` gates to
# render at all — otherwise those components silently drop out of the plan and stay
# on the cluster. Record-driven rather than a glob over env/, so unrelated
# config.<whatever>.yaml an operator keeps there is never merged in.
solution_configs() {
    local f; f=$(solutions_file "$ENV_NAME")
    [[ -f "$f" ]] \
        || err "env/${ENV_NAME}/${SOLUTIONS_BASE} is missing — this env predates the provisioning record. Run:  ./$(basename "$0") init <layer> --env ${ENV_NAME}"
    # Checked rather than ignored: an older record's fields are named differently, and
    # reading them as absent would quietly drop the pin checks instead of failing.
    local schema; schema=$(yq -r '.schema // 0' "$f" 2>/dev/null || echo 0)
    [[ "$schema" == "$SOLUTIONS_SCHEMA" ]] \
        || err "env/${ENV_NAME}/${SOLUTIONS_BASE} is schema ${schema}, this installer writes ${SOLUTIONS_SCHEMA}. Re-run:  ./$(basename "$0") init <layer> --env ${ENV_NAME}  (it rewrites the record, keeping your configs)"
    local name cfg
    while IFS='|' read -r name cfg; do
        [[ -z "$cfg" ]] && continue
        [[ -f "${ENV_DIR}/${cfg}" ]] \
            || err "Layer '${name}' was provisioned with ${cfg}, but env/${ENV_NAME}/${cfg} is gone. Restore it or re-run init."
        printf '%s\n' "${ENV_DIR}/${cfg}"
    done < <(yq -r '.layers[] | [(.name // ""), (.config // "")] | join("|")' "$f" 2>/dev/null)
}

# solution_layers — layer names in the record, one per line. Preflight refuses a plan that
# reaches any solution layer not listed here.
solution_layers() {
    local f; f=$(solutions_file "$ENV_NAME")
    [[ -f "$f" ]] || return 0
    yq -r '.layers[].name' "$f" 2>/dev/null || true
}

# _ext_roles_dirs — roles/ beside every ext components.yaml. Same discovery as
# _merged_components (and as preflight), so the roles path and the merged registry
# cannot disagree: a component in the plan always has a findable role.
_ext_roles_dirs() {
    [[ -d "$EXT_DIR" ]] || return 0
    local m d
    while IFS= read -r -d '' m; do
        d="$(dirname "$m")/roles"
        [[ -d "$d" ]] && printf '%s\n' "$d"
    done < <(find -L "$EXT_DIR" -name components.yaml -type f \
                  -not -path '*/\.git/*' -print0 2>/dev/null | sort -z)
    return 0
}

# =============================================================================
# Git operations on ext/
# =============================================================================
# Every rev form the manifests accept resolves through _checkout_rev, so a fresh clone
# and an --upgrade can never disagree about what a rev means.

# _checkout_rev <repo> <rev> <dest> — land an existing clone on a rev: a branch, tag,
# commit SHA, or full ref. A branch becomes a local branch and stays committable;
# everything else can only detach. Callers guarantee the worktree is safe to move.
_checkout_rev() {
    local repo="$1" rev="$2" dest="$3"
    if [[ "$rev" == refs/* ]]; then
        # A full ref (refs/pull/N/head) is not covered by the default fetch refspec,
        # so it has to be named explicitly and taken from FETCH_HEAD.
        git -C "$repo" fetch origin "$rev" \
            && git -C "$repo" checkout --detach FETCH_HEAD \
            && return 0
        err "cannot check out ref '${rev}' in ext/${dest}. Verify it exists on the remote."
    fi
    git -C "$repo" fetch --tags --force origin \
        || err "git fetch failed for ext/${dest}. Check credentials, network, and proxy."
    # Branch before tag: when a name is both, the committable one is meant.
    if git -C "$repo" show-ref --verify --quiet "refs/remotes/origin/${rev}"; then
        # -B moves the branch pointer, orphaning any local commit on it.
        local _ahead=0
        git -C "$repo" show-ref --verify --quiet "refs/heads/${rev}" \
            && _ahead=$(git -C "$repo" rev-list --count \
                            "refs/remotes/origin/${rev}..refs/heads/${rev}" 2>/dev/null || echo 0)
        [[ "$_ahead" == "0" ]] \
            || err "ext/${dest} branch '${rev}' has ${_ahead} commit(s) not on origin. Push or drop them, then re-run --upgrade."
        git -C "$repo" checkout -B "$rev" "refs/remotes/origin/${rev}" \
            || err "git checkout of branch '${rev}' failed for ext/${dest}."
    elif git -C "$repo" show-ref --verify --quiet "refs/tags/${rev}"; then
        git -C "$repo" checkout --detach "refs/tags/${rev}" \
            || err "git checkout of tag '${rev}' failed for ext/${dest}."
    elif git -C "$repo" rev-parse --verify --quiet "${rev}^{commit}" >/dev/null; then
        # A commit SHA, full or abbreviated. The clone above is not shallow, so anything
        # reachable from a branch or tag is already local and needs no fetch by SHA; a
        # commit on no published ref is absent and falls through to the error below.
        git -C "$repo" checkout --detach "$rev" \
            || err "git checkout of commit '${rev}' failed for ext/${dest}."
    else
        err "rev '${rev}' does not resolve in ext/${dest} — not a branch or tag on the remote, and not a commit in its history. Use a branch, a tag, a commit SHA, or a full ref such as refs/pull/N/head."
    fi
}

# clone_repo <url> <dest_path> <rev> <name> — clone unless already present.
clone_repo() {
    local url="$1" repo="$2" rev="$3" dest="$4"
    command -v git &>/dev/null \
        || err "git is required to clone ${dest} but is not installed. Install git or run './es_auto_installer.sh configure'."
    mkdir -p "$EXT_DIR"
    info "Cloning ${dest} (${rev})"
    # Cloned bare of any rev, then positioned through the same resolver --upgrade uses, so
    # the two cannot drift on what a rev may be. `git clone --branch` is avoided because it
    # takes only a branch or tag, and its failure mode is a silent checkout of the default
    # branch, i.e. deploying a revision nobody asked for.
    git clone "$url" "$repo" \
        || { rm -rf "$repo"; err "git clone failed for ${url}. Check credentials, network, and proxy."; }
    # Subshell so _checkout_rev's err exits it rather than the script, leaving this the
    # chance to clean up. A clone left at the default branch would look complete to
    # ensure_repos, which then skips cloning and seeds from the wrong revision.
    ( _checkout_rev "$repo" "$rev" "$dest" ) \
        || { rm -rf "$repo"; err "removed the partial clone ext/${dest} — nothing is left at an unintended rev."; }
}

# repin_repo <dest> <rev> — move an existing clone onto a new rev. Refuses on a dirty
# worktree, so a developer's local edits are never discarded while a clean clone moves
# freely.
repin_repo() {
    local dest="$1" rev="$2"
    local repo="${EXT_DIR}/${dest}"
    [[ -d "$repo" ]] || return 0   # not cloned yet — ensure_repos clones it at the new rev
    [[ -d "${repo}/.git" ]] \
        || { warn "ext/${dest} is not a git checkout — leaving it alone."; return 0; }
    # -uno: untracked files survive a checkout untouched, so they are not at risk and
    # must not block — an ext repo under development usually has some.
    [[ -z "$(git -C "$repo" status --porcelain -uno)" ]] \
        || err "ext/${dest} has uncommitted changes to tracked files. Commit, stash or revert them, then re-run --upgrade."

    _checkout_rev "$repo" "$rev" "$dest"
    ok "  repinned: ext/${dest} → ${rev}"
}

# report_config_delta <live> <seed> <layer> — top-level key diff, printed only.
#
# Never writes: overwriting the live config would discard operator edits, and merging
# YAML is a rabbit hole. Top-level keys are the right granularity because config vars
# are flat by convention. yq runs twice per side rather than once into a variable
# because an empty key set would then reach comm as a single blank line and be reported
# as a real difference.
report_config_delta() {
    local live="$1" seed="$2" layer="$3"
    [[ -f "$live" && -f "$seed" ]] || return 0
    local added removed
    added=$(comm -13 <(yq -r 'keys | .[]' "$live" 2>/dev/null | sort) \
                     <(yq -r 'keys | .[]' "$seed" 2>/dev/null | sort) | tr '\n' ' ')
    removed=$(comm -23 <(yq -r 'keys | .[]' "$live" 2>/dev/null | sort) \
                       <(yq -r 'keys | .[]' "$seed" 2>/dev/null | sort) | tr '\n' ' ')
    [[ -z "$added" && -z "$removed" ]] && return 0
    warn "config.${layer}.yaml differs from the new ${seed#${EXT_DIR}/}:"
    [[ -n "$added" ]]   && info "    new upstream keys, not in yours:  ${added% }"
    [[ -n "$removed" ]] && info "    yours only, dropped upstream:     ${removed% }"
    info "    Your file is untouched. Diff it against ${seed#${EXT_DIR}/} and merge by hand."
    return 0
}

# =============================================================================
# Environment, dependencies, roles path
# =============================================================================
# ensure_repos <target> [--clone] — make sure ext/ holds what the run needs, then
# build ANSIBLE_ROLES_PATH.
#   init (--clone):  clone whatever the target's manifest lists, at its pinned rev.
#   otherwise:       nothing is cloned — only init may mutate ext/. A repo this env
#                    was provisioned with but that is gone is fatal for a layer
#                    target that owns a manifest, a warning otherwise.
ensure_repos() {
    local target="${1:-}" mode="${2:-}"
    local roles="${SCRIPT_DIR}/roles"
    command -v yq &>/dev/null || { export ANSIBLE_ROLES_PATH="$roles"; return 0; }

    local layer_name url dest rev subdir _cfgdir _flav _catalog repo roles_dir
    if [[ "$mode" == "--clone" ]]; then
        local mf; mf=$(require_layer "$target")
        while IFS='|' read -r layer_name url dest rev subdir _cfgdir _flav _catalog; do
            [[ -z "$url" || -z "$dest" ]] && continue
            repo="${EXT_DIR}/${dest}"
            roles_dir="${repo}${subdir:+/$subdir}/roles"
            [[ -d "$roles_dir" ]] || clone_repo "$url" "$repo" "$rev" "$dest"
            [[ -d "$roles_dir" ]] \
                || warn "${dest} has no roles/ at ${roles_dir#${SCRIPT_DIR}/} — check deployment_subdir in repos.${layer_name}.yaml; ansible will not find its roles."
        done < <(manifest_rows "$mf")
    else
        local fatal; fatal=$(layer_manifest "$target")
        local f; f=$(solutions_file "$ENV_NAME")
        while IFS='|' read -r layer_name dest; do
            [[ -z "$dest" || -d "${EXT_DIR}/${dest}" ]] && continue
            [[ -n "$fatal" ]] \
                && err "Layer '${target}' needs ext/${dest} but it is not cloned. Run:  ./$(basename "$0") init ${target} --env ${ENV_NAME}"
            warn "env/${ENV_NAME} was provisioned with ext/${dest} but it is not on disk — layer '${layer_name}' will fail if the plan reaches it."
        done < <([[ -f "$f" ]] && yq -r '.layers[] | [(.name // ""), (.repo // "")] | join("|")' "$f" 2>/dev/null)
    fi

    # Roles live beside components.yaml, so this is the same discovery preflight does.
    while IFS= read -r roles_dir; do
        [[ ":${roles}:" == *":${roles_dir}:"* ]] || roles="${roles}:${roles_dir}"
    done < <(_ext_roles_dirs)
    export ANSIBLE_ROLES_PATH="$roles"
}

# Idempotent — no-op once $VENV has ansible-playbook. Called by configure and,
# lazily, by ensure_deps. Always exports PATH so venv tools win afterward.
_ensure_venv() {
    if [[ -x "$VENV/bin/ansible-playbook" ]]; then
        info "Using existing venv: $VENV"
        export PATH="$VENV/bin:$PATH"
        return
    fi

    local py
    py=$(_find_python) || { _python_hint; err "Python ≥ 3.11 missing."; }

    if ! "$py" -c 'import ensurepip' &>/dev/null; then
        local pv; pv=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        _venv_hint "$pv"; err "Python venv module missing."
    fi

    infon "Creating venv"
    _quiet "$py" -m venv "$VENV" \
        || err "venv creation failed. Ensure the python venv module is installed (see hint above)."
    infon "Installing Python dependencies"
    _quiet bash -c "'$VENV/bin/pip' install --upgrade pip && '$VENV/bin/pip' install -r '$SCRIPT_DIR/requirements.txt'" \
        || err "pip install failed (network/proxy issue?)."

    if [[ -f "$SCRIPT_DIR/collections/requirements.yml" ]]; then
        local pv; pv=$("$VENV/bin/python3" -c 'import sys; print(f"python{sys.version_info.major}.{sys.version_info.minor}")')
        infon "Installing Ansible collections"
        _quiet "$VENV/bin/ansible-galaxy" collection install \
            -r "$SCRIPT_DIR/collections/requirements.yml" --force \
            -p "$VENV/lib/${pv}/site-packages" \
            || err "ansible-galaxy collection install failed (network/proxy issue?)."
    fi
    export PATH="$VENV/bin:$PATH"
    ok "Ansible $(ansible --version | head -1)"
}

# ensure_deps: verify host prerequisites, then venv + pip + collections.
ensure_deps() {
    local _tool
    for _tool in yq kubectl helm; do
        command -v "$_tool" &>/dev/null \
            || err "${_tool} missing. Run './es_auto_installer.sh configure' or install it manually."
    done

    _ensure_venv
}

# ensure_sudo: detect whether passwordless sudo is available on localhost.
# Sets _NEED_BECOME_PASS=true when a password is required so run() can
# append --ask-become-pass to the ansible-playbook invocation.
# For remote nodes, become credentials must be in the inventory
# (ansible_become_password per host, or NOPASSWD sudoers + SSH key).
_NEED_BECOME_PASS=false
ensure_sudo() {
    [[ $EUID -eq 0 ]] && return 0
    # Reset cached timestamp so we test actual NOPASSWD config, not a leftover
    # session from configure or earlier sudo use.
    sudo -k 2>/dev/null
    sudo -n true &>/dev/null && return 0
    _NEED_BECOME_PASS=true
    info "Sudo password needed for privilege escalation (Ansible will prompt via -K)"
    warn "To avoid prompts: add NOPASSWD to /etc/sudoers for $USER"
}

ensure_env_dir() {
    local name="$1"
    ENV_DIR="${ENV_ROOT}/${name}"
    if [[ ! -d "$ENV_DIR" ]]; then
        local available
        available=$(find "$ENV_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f ' 2>/dev/null)
        if [[ -n "$available" ]]; then
            err "env/${name} does not exist. Available: ${available}— use --env <name>, e.g.: ./es_auto_installer.sh ${ACTION:-install} --env ${available%% *} ${TARGET:-}"
        else
            err "env/${name} does not exist. Run:  ./es_auto_installer.sh init <layer> --env ${name}"
        fi
    fi
    [[ -f "${ENV_DIR}/${SOLUTIONS_BASE}" ]] \
        || err "env/${name}/${SOLUTIONS_BASE} is missing — this env predates the provisioning record, so there is no way to know which configs it needs. Run:  ./es_auto_installer.sh init <layer> --env ${name}  (it fills gaps only, existing files are kept)"
    ENV_LOG_DIR="${ENV_DIR}/logs"
    ENV_INVENTORY_DIR="${ENV_DIR}/inventory"
    GLOBAL_CONFIG="${ENV_DIR}/global_config.yaml"
    mkdir -p "$ENV_LOG_DIR" "$ENV_INVENTORY_DIR"
}

resolve_inventory() {
    local f="${ENV_INVENTORY_DIR}/hosts.yaml"
    [[ -f "$f" ]] || err "Inventory missing: ${f}. Run: ./es_auto_installer.sh init <layer> --env ${ENV_NAME}"
    INVENTORY="$f"
    info "Inventory: $INVENTORY"
}

# =============================================================================
# init
# =============================================================================
# _init_reconcile_pins <sol> <manifest> <layer> <upgrade> — make ext/ agree with the
# manifest, or refuse. Every rev is checked before any repo moves, so an init this env
# cannot accept changes nothing at all.
_init_reconcile_pins() {
    local sol="$1" mf="$2" layer="$3" upgrade="$4"
    local layer_name dest rev rest prev
    local -a repin=()

    while IFS='|' read -r layer_name _ dest rev rest; do
        [[ -z "$layer_name" || -z "$dest" ]] && continue
        prev=$(_solutions_get "$sol" "$layer_name" rev)
        [[ -z "$prev" || "$prev" == "$rev" ]] && continue
        [[ "$upgrade" == "true" ]] \
            || err "Layer '${layer_name}' was provisioned at '${prev}' but repos.${layer}.yaml now pins '${rev}'. Re-run with --upgrade to move ext/${dest} (refused if it has local changes), or init into a fresh --env."
        # Checked here as well as in repin_repo: refusing during application would leave
        # ext/ half-moved with the record still describing the old revs.
        [[ ! -d "${EXT_DIR}/${dest}/.git" ]] \
            || [[ -z "$(git -C "${EXT_DIR}/${dest}" status --porcelain -uno)" ]] \
            || err "ext/${dest} has uncommitted changes to tracked files. Commit, stash or revert them, then re-run --upgrade."
        info "  re-pin: ${layer_name}  ${prev} → ${rev}"
        repin+=("${dest}|${rev}")
    done < <(manifest_rows "$mf")

    if [[ "$upgrade" == "true" && ${#repin[@]} -eq 0 ]]; then
        [[ -f "$sol" ]] \
            && info "  nothing to re-pin: every recorded rev already matches ${mf##*/}." \
            || warn "  no provisioning record yet, so there is no previous state to move from — writing one now."
    fi

    local pair
    for pair in ${repin[@]+"${repin[@]}"}; do
        repin_repo "${pair%%|*}" "${pair#*|}"
    done
}

# _init_seed_defaults <env_dir> <name> — copy configs/defaults/*.yaml into the env,
# never over an existing file.
_init_seed_defaults() {
    local env_dir="$1" name="$2"
    local f base
    for f in "$DEFAULTS_DIR"/*.yaml; do
        [[ -e "$f" ]] || continue
        base=$(basename "$f")
        if [[ -f "$env_dir/$base" ]]; then
            warn "  exists, skipping: $base"
        else
            cp "$f" "$env_dir/$base"
            info "  created: ${name}/${base}"
        fi
    done
}

# _init_warn_cross_env <name> <layer> <dest> <rev> — another env in this checkout
# wanting the same repo at a different rev. ext/ is per checkout and can only be at one,
# so this is reported rather than reconciled.
_init_warn_cross_env() {
    local name="$1" layer_name="$2" dest="$3" rev="$4"
    local other base other_rev
    for other in "$ENV_ROOT"/*/"$SOLUTIONS_BASE"; do
        [[ -f "$other" ]] || continue
        base=$(basename "$(dirname "$other")")
        [[ "$base" == "$name" ]] && continue
        other_rev=$(_solutions_get "$other" "$layer_name" rev)
        [[ -n "$other_rev" && "$other_rev" != "$rev" ]] \
            && warn "  env/${base} is provisioned with ext/${dest} at '${other_rev}' — ext/ can only be at one rev."
    done
    return 0
}

# _init_flavours <flavour_dir> — flavour names under a config_dir, one per line.
# examples/ and anything starting with _ or . are shared or scratch, never presets.
_init_flavours() {
    local d name
    for d in "$1"/*/; do
        [[ -d "$d" ]] || continue
        name=$(basename "$d")
        [[ "$name" == "examples" || "$name" == _* || "$name" == .* ]] && continue
        printf '%s\n' "$name"
    done
    return 0
}

# _init_resolve_seed <layer> <repo_dir> <config_dir> <requested_flavour> <default_flavour>
# — echo the config.yaml a fresh seed must be copied from.
#
# A layer with a config_dir is seeded from a flavour and never from the repo-root
# config.yaml: that file predates flavours in some repos, so falling back to it would
# silently seed a stale config.
_init_resolve_seed() {
    local layer_name="$1" repo_dir="$2" config_dir="$3" req_flavour="$4" default_flavour="$5"
    local cfg

    if [[ -z "$config_dir" ]]; then
        [[ -n "$req_flavour" ]] \
            && err "Layer '${layer_name}' has no flavours (no config_dir in repos.${layer_name}.yaml); drop --flavour."
        cfg="${repo_dir}/config.yaml"
    else
        local flavour_dir="${repo_dir}/${config_dir}"
        local available; available=$(_init_flavours "$flavour_dir" | tr '\n' ' ')
        [[ -z "$req_flavour" || -f "$flavour_dir/$req_flavour/config.yaml" ]] \
            || err "Unknown flavour: '${req_flavour}'. Available for ${layer_name}: ${available:-<none>} (from ${config_dir}/ in ext repo)."
        local use="${req_flavour:-$default_flavour}"
        [[ -n "$use" ]] \
            || err "Layer '${layer_name}' declares config_dir '${config_dir}' but no default_flavour in repos.${layer_name}.yaml — pass --flavour. Available: ${available:-<none>}."
        cfg="$flavour_dir/$use/config.yaml"
        [[ -f "$cfg" ]] \
            || err "Flavour '${use}' for layer '${layer_name}' has no config.yaml at ${cfg#${EXT_DIR}/}. Available: ${available:-<none>}."
    fi

    [[ -f "$cfg" ]] \
        || err "Layer '${layer_name}': nothing to seed config.${layer_name}.yaml from — ${cfg#${EXT_DIR}/} does not exist."
    printf '%s' "$cfg"
}

# _init_check_flavour <layer> <seeded_from> <subdir> <config_dir> <flavour> <name>
# — refuse a --flavour that is not the one the live config came from.
#
# Switching flavour would discard the operator's edits, so it is never done implicitly.
# Compared as paths so no flavour name has to be parsed back out of the record: note
# seeded_from is repo-root relative while config_dir is relative to deployment_subdir.
_init_check_flavour() {
    local layer_name="$1" seeded_from="$2" subdir="$3" config_dir="$4" flavour="$5" name="$6"
    [[ -n "$flavour" && -n "$config_dir" ]] || return 0

    if [[ -z "$seeded_from" || "$seeded_from" == "unknown" ]]; then
        warn "  config.${layer_name}.yaml has no recorded source, so --flavour ${flavour} cannot be verified against it."
        return 0
    fi
    local want="${subdir:+${subdir}/}${config_dir}/${flavour}/config.yaml"
    [[ "$seeded_from" == "$want" ]] \
        || err "config.${layer_name}.yaml was seeded from ${seeded_from}, but --flavour ${flavour} asks for ${want}. Switching flavour would discard your edits to it, so init will not: use a fresh --env, or delete env/${name}/config.${layer_name}.yaml to reseed."
}

# _init_seed_layer <manifest_row> <name> <layer> <flavour> <upgrade> <sol> <sol_new>
# — one repo from the manifest: seed its config and models.yaml if absent, and record
# what was provisioned. Reads the previous record ($sol) and writes the new one
# ($sol_new), so a re-init preserves fields it cannot recompute.
_init_seed_layer() {
    local row="$1" name="$2" layer="$3" flavour="$4" upgrade="$5" sol="$6" sol_new="$7"
    local layer_name url dest rev subdir config_dir default_flavour catalog
    IFS='|' read -r layer_name url dest rev subdir config_dir default_flavour catalog <<< "$row"
    [[ -z "$layer_name" || -z "$dest" ]] && return 0

    local repo_dir="${EXT_DIR}/${dest}${subdir:+/$subdir}"
    local target="${ENV_DIR}/config.${layer_name}.yaml"
    local seeded_from; seeded_from=$(_solutions_get "$sol" "$layer_name" seeded_from)

    # A flavour requested on the CLI belongs to the primary layer only.
    local req_flavour=""
    [[ "$layer_name" == "$layer" ]] && req_flavour="$flavour"

    if [[ -f "$target" ]]; then
        warn "  exists, skipping: config.${layer_name}.yaml"
        _init_check_flavour "$layer_name" "$seeded_from" "$subdir" \
                            "$config_dir" "$req_flavour" "$name"
        # --upgrade never rewrites a live config — that would discard operator edits.
        # Report what moved upstream and let them merge.
        if [[ "$upgrade" == "true" ]]; then
            local new_seed="${EXT_DIR}/${dest}/${seeded_from}"
            if [[ -z "$seeded_from" || "$seeded_from" == "unknown" ]]; then
                warn "  config.${layer_name}.yaml has no recorded source — cannot diff it against ${rev}."
            elif [[ -f "$new_seed" ]]; then
                report_config_delta "$target" "$new_seed" "$layer_name"
            else
                warn "  ${dest}/${seeded_from} no longer exists at '${rev}' — cannot diff config.${layer_name}.yaml."
            fi
        fi
    else
        local cfg; cfg=$(_init_resolve_seed "$layer_name" "$repo_dir" \
                                            "$config_dir" "$req_flavour" "$default_flavour")
        cp "$cfg" "$target"
        info "  created: ${name}/config.${layer_name}.yaml  (${cfg#${EXT_DIR}/})"
        seeded_from="${cfg#${EXT_DIR}/${dest}/}"
    fi
    # Only reachable for a config that already existed before any record did.
    [[ -n "$seeded_from" ]] || seeded_from="unknown"

    if [[ -n "$catalog" ]]; then
        local mc_source="${EXT_DIR}/${dest}/${catalog}"
        [[ -f "$mc_source" ]] \
            || err "Layer '${layer_name}' declares model_catalog '${catalog}' but ${dest}/${catalog} does not exist."
        if [[ -f "${ENV_DIR}/models.yaml" ]]; then
            warn "  exists, skipping: models.yaml"
        else
            cp "$mc_source" "${ENV_DIR}/models.yaml"
            info "  created: ${name}/models.yaml  (from ${dest}/${catalog})"
        fi
    fi

    # seeded_by is first-writer: with two solutions sharing a dependency, the second
    # init found the config already there and seeded nothing.
    local seeded_by; seeded_by=$(_solutions_get "$sol" "$layer_name" seeded_by)
    [[ -n "$seeded_by" ]] || seeded_by="$layer"

    SL_NAME="$layer_name" SL_CONFIG="config.${layer_name}.yaml" SL_SEEDED_FROM="$seeded_from" \
    SL_REPO="$dest" SL_URL="$url" SL_REV="$rev" SL_MC="$catalog" SL_BY="$seeded_by" \
        _solutions_upsert "$sol_new"

    _init_warn_cross_env "$name" "$layer_name" "$dest" "$rev"
}

# init — create env/<name>, seed its configs, and record what was provisioned.
# Usage: init <layer> [--env <name>] [--flavour <name>] [--upgrade]
#
# Reads INIT_LAYER / INIT_FLAVOUR / INIT_UPGRADE / ENV_NAME from parse_args. Every repo
# in the layer's manifest is cloned at its pinned rev and gets a config.<layer>.yaml
# seeded; --flavour picks a preset from the primary layer's config_dir and defaults to
# its default_flavour. --upgrade moves already-cloned repos onto the manifest's current
# revs. Terminal: main exits straight after, which is what lets the EXIT trap below
# stand uncleared.
init() {
    local name="$ENV_NAME" layer="$INIT_LAYER" flavour="$INIT_FLAVOUR" upgrade="$INIT_UPGRADE"

    _require_yq
    [[ -n "$layer" ]] \
        || err "init requires a layer. Valid: $(known_layers | tr '\n' ' ')(try: ./$(basename "$0") init $(known_layers | head -1) --env ${name})"
    local mf; mf=$(require_layer "$layer")
    [[ -z "$mf" && -n "$flavour" ]] \
        && err "Layer '${layer}' has no external repos, so no flavours; drop --flavour."

    ENV_DIR="${ENV_ROOT}/${name}"
    local sol; sol=$(solutions_file "$name")
    local mode=""; [[ "$upgrade" == "true" ]] && mode="  (upgrade)"
    info "init: env=${name}  layer=${layer}${flavour:+  flavour=${flavour}}${mode}  →  ${ENV_DIR}"
    mkdir -p "$ENV_DIR/inventory" "$ENV_DIR/logs"

    local inv_file="$ENV_DIR/inventory/hosts.yaml"
    if [[ ! -f "$inv_file" ]]; then
        cp "${SCRIPT_DIR}/inventory/hosts.yaml" "$inv_file"
        info "  created: ${name}/inventory/hosts.yaml"
    fi

    _init_reconcile_pins "$sol" "$mf" "$layer" "$upgrade"
    ensure_repos "$layer" --clone
    _init_seed_defaults "$ENV_DIR" "$name"

    # The record is built aside and moved into place once seeding has finished. One left
    # behind by a run that failed halfway is worse than none: it would satisfy the load
    # check while naming no configs, so every `enabled:` gate would fall to its default
    # and its components would silently drop out of the plan.
    local sol_new="${sol}.tmp"
    rm -f "$sol_new"
    trap "rm -f '$sol_new'" EXIT   # EXIT, not RETURN: err exits rather than returns
    if [[ -f "$sol" ]]; then cp "$sol" "$sol_new"; else _solutions_create "$sol_new"; fi
    SL_LAYER="$layer" SL_SCHEMA="$SOLUTIONS_SCHEMA" yq -i \
        '.schema = (strenv(SL_SCHEMA) | tonumber)
         | .inited = ((.inited // []) + [strenv(SL_LAYER)] | unique)' "$sol_new"

    local row
    while IFS= read -r row; do
        [[ -n "$row" ]] || continue
        _init_seed_layer "$row" "$name" "$layer" "$flavour" "$upgrade" "$sol" "$sol_new"
    done < <(manifest_rows "$mf")

    mv "$sol_new" "$sol"
    ok "init: done."
    info "  Configure  ${ENV_DIR}/global_config.yaml  (see README.md § Configuration Reference)"
    [[ -f "${ENV_DIR}/models.yaml" ]] \
        && info "  Configure  ${ENV_DIR}/models.yaml          (see README.md § Deploy a Model)"
    info "  Then       ./$(basename "$0") install --env ${name} ${layer}"
}

# =============================================================================
# Ansible invocation
# =============================================================================
# run: ansible-playbook wrapper that always passes -i.
run() {
    local pb_name="$1"; shift
    local pb="$SCRIPT_DIR/playbooks/${pb_name}.yaml"
    [[ -f "$pb" ]] || err "Playbook not found: $pb"

    local -a become_flag=()
    [[ "$_NEED_BECOME_PASS" == "true" ]] && become_flag=(--ask-become-pass)

    local -a verbose=()
    (( _LOG_LEVEL >= 1 )) && verbose=(-vvv)

    PYTHONUNBUFFERED=1 ansible-playbook -i "$INVENTORY" "$pb" "${become_flag[@]}" "${verbose[@]}" "$@" 2>&1 | tee -a "$LOG" \
        || err "${pb_name} failed. Log: $LOG"
}

# run_kubespray: execute a kubespray playbook directly from bash so the
# operator gets live streaming output. Ansible-in-ansible swallows all
# progress, so the kubernetes role only does prep; this function bridges
# prep → {cluster,reset,scale,remove-node}.yml → post.
#   install:  cluster.yml
#   teardown: reset.yml            -e reset_confirmation=yes
#   scale:    scale.yml            --limit=<arg2>,kube_control_plane (arg2: new worker(s))
#   remove:   remove-node.yml      -e node=<arg2> -e skip_confirmation=true
run_kubespray() {
    local action="${1:-install}" arg2="${2:-}"
    local ks_dir="${SCRIPT_DIR}/.kubespray"
    if [[ ! -d "$ks_dir/venv" || ! -f "$ks_dir/cluster.yml" ]]; then
        err "Kubespray not found at $ks_dir (missing venv or cluster.yml). Run 'install kubernetes' first."
    fi

    # Inventory resolution: the env inventory IS the kubespray inventory
    # (kubespray-compatible format with kube_control_plane/kube_node/etcd groups).
    # Fall back to the generated mycluster inventory for localhost mode.
    local inv="$INVENTORY"
    [[ -f "$inv" ]] || inv="${ks_dir}/inventory/mycluster/hosts.yaml"  # kubespray's own sample
    [[ -f "$inv" ]] || { warn "Inventory not found: $inv — skipping kubespray"; return 0; }

    local pb="$ks_dir/cluster.yml" extra=() limit_args=()
    case "$action" in
        teardown) pb="$ks_dir/reset.yml";       extra=(-e reset_confirmation=yes) ;;
        scale)    pb="$ks_dir/scale.yml";       limit_args=(--limit="${arg2},kube_control_plane") ;;
        remove)   pb="$ks_dir/remove-node.yml"; extra=(-e "node=${arg2}" -e skip_confirmation=true) ;;
    esac
    [[ -f "$ENV_DIR/kubespray_extra_auto.yml" ]] && extra+=(-e "@$ENV_DIR/kubespray_extra_auto.yml")
    [[ -f "$ENV_DIR/kubespray_extra.yml" ]] && extra+=(-e "@$ENV_DIR/kubespray_extra.yml")

    local -a ks_become_flag=()
    [[ "$_NEED_BECOME_PASS" == "true" ]] && ks_become_flag=(--ask-become-pass)

    local -a verbose=()
    (( _LOG_LEVEL >= 1 )) && verbose=(-vvv)

    info "Kubespray $(basename "$pb") — live output"
    # Run in a subshell from kubespray's directory. ANSIBLE_ROLES_PATH must be
    # unset (not empty) so ansible.cfg's roles_path takes effect. Empty string
    # overrides the cfg with "nothing"; unset falls through to cfg.
    # ANSIBLE_HOST_KEY_CHECKING=False disables SSH host-key verification for the
    # cluster play — fine for localhost/lab, but revisit once real remote nodes
    # are in the inventory.
    (cd "$ks_dir" && \
     unset ANSIBLE_ROLES_PATH KUBECONFIG K8S_AUTH_KUBECONFIG && \
     ANSIBLE_CONFIG="$ks_dir/ansible.cfg" \
     ANSIBLE_FORCE_COLOR=true \
     ANSIBLE_HOST_KEY_CHECKING=False \
     PATH="$ks_dir/venv/bin:$PATH" \
        "$ks_dir/venv/bin/ansible-playbook" -i "$inv" \
        --become --become-user=root "${ks_become_flag[@]}" "${verbose[@]}" \
        "${limit_args[@]+"${limit_args[@]}"}" \
        "$pb" "${extra[@]+"${extra[@]}"}" 2>&1) | tee -a "$LOG" \
        || err "Kubespray failed. Log: $LOG"

    # Kubespray writes admin.conf to artifacts_dir (=ENV_DIR) via kubeconfig_localhost.
    # Rename to kubeconfig.yaml so the rest of the installer finds it.
    local _kc="${ENV_DIR}/kubeconfig.yaml"
    if [[ -f "$ENV_DIR/admin.conf" ]]; then
        mv "$ENV_DIR/admin.conf" "$_kc"
        chmod 600 "$_kc"
        ok "Kubeconfig written to ${_kc}"
    fi
}

# =============================================================================
# Node scaling — add-node / remove-node
# =============================================================================
# Neither action takes a node name on the CLI: the operator edits
# env/<env>/nodes.yaml (add or remove entries), and the node(s) to act on are
# found by diffing nodes.yaml's declared hostnames against the live cluster's
# actual node list — read directly from nodes.yaml, not from the rendered
# inventory, so the diff needs no Ansible run and --dry-run touches nothing.
# Regenerating the on-disk inventory happens later, and at a different point
# for each action (see do_add_node/do_remove_node). Both assume
# vars[]/EXTRA_VARS/INVENTORY/ENV_DIR etc. are already set by main(), same as
# the rest of the install/teardown path.

# _nodes_yaml_hostnames [control_plane|workers] — hostnames from nodes.yaml,
# one per line. No argument means both lists combined.
_nodes_yaml_hostnames() {
    local expr
    case "${1:-}" in
        control_plane) expr='(.nodes_control_plane // [])' ;;
        workers)       expr='(.nodes_workers // [])' ;;
        *)             expr='((.nodes_control_plane // []) + (.nodes_workers // []))' ;;
    esac
    yq -r "${expr} | .[].hostname" "$ENV_DIR/nodes.yaml" 2>/dev/null
}

# _require_nodes_yaml_defined — add-node/remove-node only make sense when
# nodes.yaml is the source of truth for the inventory (the hand-edited-hosts.yaml
# mode this installer also supports has no per-node structure to diff against).
_require_nodes_yaml_defined() {
    [[ -f "$ENV_DIR/nodes.yaml" ]] && [[ -n "$(_nodes_yaml_hostnames control_plane)" ]] \
        || err "${ACTION} requires nodes_control_plane to be defined in env/${ENV_NAME}/nodes.yaml. A hand-edited inventory (env/${ENV_NAME}/inventory/hosts.yaml) has no per-node structure to diff against — ${ACTION} does not support that mode."
}

# _inventory_group <group> — hostnames declared under all.children.<group>.hosts
# in $INVENTORY, one per line.
_inventory_group() {
    yq -r ".all.children.${1}.hosts // {} | keys | .[]" "$INVENTORY" 2>/dev/null
}

# _live_nodes — Node object names in the live cluster, one per line.
# KUBECONFIG is already exported by main() before either action runs.
_live_nodes() {
    kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null
}

# _node_workloads <node> — "namespace/name" of every real workload pod
# scheduled on <node>, one per line. Excludes DaemonSet pods (recreated
# automatically wherever they're needed) and Node-owned static/mirror pods
# (e.g. kubespray's nginx-proxy — infrastructure that lives and dies with the
# node itself, not something `kubectl drain` even evicts) — neither is the
# kind of workload an operator needs a warning about before removing a node.
_node_workloads() {
    kubectl get pods -A --field-selector "spec.nodeName=${1}" -o json 2>/dev/null \
        | yq -p json -r '.items[]
            | select((.metadata.ownerReferences // []) | map(.kind == "DaemonSet" or .kind == "Node") | any | not)
            | .metadata.namespace + "/" + .metadata.name' 2>/dev/null || true
}

# _cp_tainted_nodes — names of control-plane nodes that currently carry the
# standard kubeadm NoSchedule taint, one per line. A control-plane node
# provisioned single-node (in kube_node at kubeadm-init time) never gets this
# taint even after workers are added later via add-node — it's set once at
# init, not reconciled — so this has to be checked live, not assumed from
# group membership.
_cp_tainted_nodes() {
    kubectl get nodes -l node-role.kubernetes.io/control-plane -o json 2>/dev/null \
        | yq -p json -r '.items[]
            | select((.spec.taints // [])
                     | map(.key == "node-role.kubernetes.io/control-plane" and .effect == "NoSchedule") | any)
            | .metadata.name' 2>/dev/null || true
}

# _pdb_blocked_pods <node> — "pod<TAB>pdb<TAB>detail" for every pod on <node>
# that a PodDisruptionBudget currently allows zero disruptions for. kubectl
# drain evicts via the Eviction API, which the API server rejects outright
# whenever a pod's PDB has disruptionsAllowed == 0 — retrying does not help,
# since that number is budget math (currentHealthy vs minAvailable), not a
# transient condition. Left unchecked, remove-node discovers this only as a
# drain that hangs through drain_retries x drain_timeout before giving up.
# PDBs that select pods via matchExpressions (no matchLabels) can't be
# resolved to a `kubectl -l` selector here, so they're reported with
# "unsupported selector" instead of being silently skipped — the caller
# should treat that as blocking too, pending manual review.
_pdb_blocked_pods() {
    local node="$1" ns name selector pod
    while IFS=$'\t' read -r ns name selector; do
        [[ -n "$ns" ]] || continue
        if [[ -z "$selector" ]]; then
            printf '%s/%s\t%s/%s\tunsupported selector (not matchLabels-based) — verify manually\n' "$ns" "$name" "$ns" "$name"
            continue
        fi
        while IFS= read -r pod; do
            [[ -n "$pod" ]] && printf '%s/%s\t%s/%s\tselector=%s\n' "$ns" "${pod#pod/}" "$ns" "$name" "$selector"
        done < <(kubectl get pods -n "$ns" -l "$selector" --field-selector "spec.nodeName=${node}" -o name 2>/dev/null)
    done < <(kubectl get pdb -A -o json 2>/dev/null | yq -p json -r '
        .items[]
        | select(.status.disruptionsAllowed == 0)
        | [.metadata.namespace, .metadata.name, ((.spec.selector.matchLabels // {}) | to_entries | map(.key + "=" + .value) | join(","))]
        | @tsv
    ' 2>/dev/null || true)
}

# _nodes_yaml_ssh_target <hostname> — "ip<TAB>user<TAB>key" for <hostname>,
# resolved from nodes.yaml with the same precedence hosts.yaml.j2 uses:
# per-node ssh_user/ssh_key override the top-level nodes_ssh_user/nodes_ssh_key,
# which in turn fall back to ssh's own defaults (current user, default identity)
# when left unset.
_nodes_yaml_ssh_target() {
    _H="$1" yq -r '
        . as $root
        | ((.nodes_control_plane // []) + (.nodes_workers // []))
        | .[] | select(.hostname == env(_H))
        | [.ip, (.ssh_user // $root.nodes_ssh_user // ""), (.ssh_key // $root.nodes_ssh_key // "")]
        | @tsv
    ' "$ENV_DIR/nodes.yaml" 2>/dev/null
}

# _ssh_preflight_check <hostname> — empty output and rc 0 if <hostname>'s
# declared node accepts the credentials nodes.yaml resolves for it; otherwise
# the raw ssh error. Kubespray's own SSH pre-flight (roles/kubernetes/tasks/
# add-node.yaml) catches an unauthorized key too, but only after
# kubespray_ensure + inventory regen + Ansible startup — this fails in under
# a second, before the operator is even asked to confirm.
_ssh_preflight_check() {
    local hostname="$1" ip user key target
    IFS=$'\t' read -r ip user key < <(_nodes_yaml_ssh_target "$hostname")
    [[ -n "$ip" ]] || { echo "no ip found for ${hostname} in nodes.yaml"; return 1; }
    target="$ip"
    [[ -n "$user" ]] && target="${user}@${ip}"
    local -a ssh_opts=(-o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new)
    [[ -n "$key" ]] && ssh_opts+=(-i "${key/#\~/$HOME}")
    ssh "${ssh_opts[@]}" "$target" true 2>&1
}

# do_add_node — scale worker node(s) into an existing cluster (Kubespray scale.yml).
do_add_node() {
    _require_nodes_yaml_defined

    local declared live added
    declared=$(_nodes_yaml_hostnames | sort -u)
    live=$(_live_nodes | sort -u)
    added=$(comm -23 <(printf '%s\n' "$declared") <(printf '%s\n' "$live") | grep -v '^$' || true)

    if [[ -z "$added" ]]; then
        ok "add-node: nothing to do — nodes.yaml matches the live cluster."
        exit 0
    fi

    local declared_cp added_cp
    declared_cp=$(_nodes_yaml_hostnames control_plane | sort -u)
    added_cp=$(comm -12 <(printf '%s\n' "$declared_cp") <(printf '%s\n' "$added") | grep -v '^$' || true)
    if [[ -n "$added_cp" ]]; then
        err "add-node only scales worker nodes. New control-plane node(s) detected: ${added_cp//$'\n'/, }
  Kubespray's scale.yml cannot add control-plane nodes. Use instead:
    ./$(basename "$0") install --env ${ENV_NAME} kubernetes"
    fi

    local added_csv="${added//$'\n'/,}"
    info "add-node: new worker node(s): ${added_csv//,/, }"

    # SSH guard: Kubespray's own pre-flight catches an unauthorized key too,
    # but only after kubespray_ensure + inventory regen + Ansible startup
    # overhead. Checked here against the real ip/user/key nodes.yaml
    # declares, so it fails in under a second instead.
    local ssh_hits="" n ssh_err
    for n in ${added//$'\n'/ }; do
        ssh_err=$(_ssh_preflight_check "$n") || ssh_hits+="  ${n}: ${ssh_err}"$'\n'
    done
    if [[ -n "$ssh_hits" ]]; then
        err "add-node: cannot SSH to the new node(s) below:
${ssh_hits}  Authorize this host's key on the target(s) (e.g. ssh-copy-id), or fix ssh_user/ssh_key in nodes.yaml, then retry."
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        info "  would run: scale.yml --limit=${added_csv},kube_control_plane"
        exit 0
    fi

    confirm "About to run Kubespray scale.yml for: ${added_csv//,/, }"
    # Regenerate the inventory to INCLUDE the new node(s) before scale.yml runs
    # — it needs their connection details to reach and join them. _added_nodes
    # is passed here too (not just on the post call) so the prep phase's own
    # SSH/internet pre-flight can scope to just the new node(s).
    run site "${vars[@]}" -e "_scale_phase=prep" -e "_added_nodes=${added_csv}" "${EXTRA_VARS[@]}"
    run_kubespray scale "$added_csv"
    run site "${vars[@]}" -e "_scale_phase=post" -e "_added_nodes=${added_csv}" "${EXTRA_VARS[@]}"
}

# do_remove_node — remove node(s) from an existing cluster (Kubespray remove-node.yml).
do_remove_node() {
    _require_nodes_yaml_defined

    # Kubespray's own notion of "first" control-plane/etcd host, read from the
    # CURRENT on-disk inventory — deliberately not regenerated at any point
    # before remove-node.yml runs (see roles/kubernetes/tasks/remove-node.yaml).
    local old_first_cp; old_first_cp=$(_inventory_group kube_control_plane | head -1)

    local declared live removed
    declared=$(_nodes_yaml_hostnames | sort -u)
    live=$(_live_nodes | sort -u)
    removed=$(comm -23 <(printf '%s\n' "$live") <(printf '%s\n' "$declared") | grep -v '^$' || true)

    if [[ -z "$removed" ]]; then
        ok "remove-node: nothing to do — nodes.yaml matches the live cluster."
        exit 0
    fi

    if [[ -n "$old_first_cp" ]] && grep -qxF -- "$old_first_cp" <<< "$removed"; then
        err "remove-node cannot remove '${old_first_cp}' — it is the first control-plane/etcd node, and Kubespray does not support removing it directly.
  Reorder nodes_control_plane in env/${ENV_NAME}/nodes.yaml so a different host is first, run:
    ./$(basename "$0") install --env ${ENV_NAME} kubernetes
  to apply the reorder via cluster.yml, then retry remove-node."
    fi

    local removed_csv="${removed//$'\n'/,}"
    info "remove-node: node(s) to remove: ${removed_csv//,/, }"

    # Workload visibility: the drain step evicts every non-DaemonSet pod on
    # the node(s) below, PDB permitting. Surfaced before --dry-run's exit and
    # before confirm() so the operator sees what's actually at stake, rather
    # than finding out via a drain hang or a workload quietly going down.
    local n workloads any_workloads=false
    for n in ${removed//$'\n'/ }; do
        workloads=$(_node_workloads "$n")
        if [[ -n "$workloads" ]]; then
            any_workloads=true
            warn "  ${n} is running workload(s) that will be evicted:"
            while IFS= read -r w; do
                [[ -n "$w" ]] && info "    - ${w}"
            done <<< "$workloads"
        fi
    done
    [[ "$any_workloads" == "true" ]] || info "  no non-DaemonSet workloads running on: ${removed_csv//,/, }"

    # PodDisruptionBudget guard: a pod whose PDB currently allows zero
    # disruptions will have every eviction attempt rejected by the API
    # server, so kubectl drain cannot succeed against it no matter how many
    # times remove-node.yml retries. Caught here, before confirm(), instead
    # of discovered later as a drain hung through drain_retries x
    # drain_timeout.
    local pdb_hits=""
    for n in ${removed//$'\n'/ }; do
        while IFS=$'\t' read -r pod pdb detail; do
            [[ -n "$pod" ]] && pdb_hits+="  ${pod} on ${n} — blocked by PodDisruptionBudget ${pdb} (${detail})"$'\n'
        done < <(_pdb_blocked_pods "$n")
    done
    if [[ -n "$pdb_hits" ]]; then
        err "remove-node: drain would hang — the pod(s) below cannot be evicted because their PodDisruptionBudget currently allows zero disruptions:
${pdb_hits}  Scale up the workload, raise/delete the PDB, or move it off the node first, then retry remove-node."
    fi

    # Last-worker warning: "worker" here means live and NOT in kube_control_plane,
    # not kube_node membership — kube_node mirrors kube_control_plane in
    # single-node mode (see hosts.yaml.j2), which would otherwise make every
    # single-node cluster look like it's losing "a worker" on its own control
    # -plane node. Only a warning: dropping to zero workers is exactly what
    # single-node mode already is, and is fine when the control-plane node
    # isn't tainted (see _cp_tainted_nodes).
    local live_cp live_workers remaining_workers
    live_cp=$(_inventory_group kube_control_plane | sort -u)
    live_workers=$(comm -23 <(printf '%s\n' "$live") <(printf '%s\n' "$live_cp") | grep -v '^$' || true)
    remaining_workers=$(comm -23 <(printf '%s\n' "$live_workers") <(printf '%s\n' "$removed") | grep -v '^$' || true)
    if [[ -n "$live_workers" && -z "$remaining_workers" ]]; then
        local tainted_cp; tainted_cp=$(_cp_tainted_nodes)
        if [[ -n "$tainted_cp" ]]; then
            warn "  this removes the last worker node. Control-plane node(s) ${tainted_cp//$'\n'/, } carry the NoSchedule taint — nothing will be schedulable for regular workloads until a worker is added back (or the taint is removed manually)."
        else
            warn "  this removes the last worker node. The control-plane node has no NoSchedule taint, so workloads can still schedule there, but this reduces the cluster to single-node capacity."
        fi
    fi

    if [[ "$DRY_RUN" == "true" ]]; then
        info "  would run: remove-node.yml -e node=${removed_csv} -e skip_confirmation=true"
        exit 0
    fi

    confirm "About to run Kubespray remove-node.yml for: ${removed_csv//,/, } — this drains, resets, and removes those node(s) from the cluster"
    # Ensure kubespray only — the inventory is NOT regenerated here on purpose;
    # remove-node.yml needs the target node(s) still present in it to reach
    # them (see roles/kubernetes/tasks/remove-node.yaml for why).
    run site "${vars[@]}" -e "_scale_phase=prep" "${EXTRA_VARS[@]}"
    run_kubespray remove "$removed_csv"
    # Only now regenerate the inventory, dropping the removed node(s).
    run site "${vars[@]}" -e "_scale_phase=post" "${EXTRA_VARS[@]}"

    for n in ${removed//$'\n'/ }; do
        rm -f "${ENV_INVENTORY_DIR}/host_vars/${n}.yml"
    done
}

# _opt_value <flag> <value> — a flag's value must exist and not be another flag,
# else `--env --flavour` silently seeds an env literally named "--flavour".
# =============================================================================
# CLI
# =============================================================================
_opt_value() {
    [[ -n "$2" && "$2" != -* ]] \
        || err "$1 requires a value (got '${2:-<nothing>}')."
}

# parse_args
parse_args() {
    ACTION="" TARGET="" ENV_NAME="local" ONLY=false FORCE=false SKIP="" DRY_RUN=false
    INIT_LAYER="" INIT_FLAVOUR="" INIT_UPGRADE=false
    EXTRA_VARS=()

    [[ $# -eq 0 || "${1:-}" =~ ^(-h|--help)$ ]] && { usage; exit 0; }
    [[ "$1" =~ ^(-v|--version)$ ]] && { echo "es_auto_installer ${INSTALLER_VERSION}"; exit 0; }

    ACTION="$1"; shift

    # Guard: action must be one of the known verbs.
    local _known=false _a
    for _a in "${ACTIONS[@]}"; do [[ "$ACTION" == "$_a" ]] && { _known=true; break; }; done
    [[ "$_known" == "true" ]] || err "Unknown action: '$ACTION'. Valid: ${ACTIONS[*]} (try --help)"

    # init uses its own grammar: init <layer> [--env <name>] [--flavour <name>]
    if [[ "$ACTION" == "init" ]]; then
        INIT_LAYER=""
        INIT_FLAVOUR=""
        while [[ $# -gt 0 ]]; do
            case "$1" in
                --env)      _opt_value --env "${2:-}"; ENV_NAME="$2"; shift 2 ;;
                --flavour)  _opt_value --flavour "${2:-}"; INIT_FLAVOUR="$2"; shift 2 ;;
                --upgrade)  INIT_UPGRADE=true; shift ;;
                --force)    FORCE=true; shift ;;
                -*)         err "Unknown option for init: '$1'. Valid: --env <name>, --flavour <name>, --upgrade, --force." ;;
                *)          [[ -z "$INIT_LAYER" ]] \
                                || err "init takes one layer, got '${INIT_LAYER}' and '$1'."
                            INIT_LAYER="$1"; shift ;;
            esac
        done
        set --
    fi

    # Unified parse for the remaining verbs. The target may appear anywhere (not
    # just first), so `install --env prod platform` works — the first bare
    # (non-dash) token becomes the target regardless of position. Unknown --flags
    # are rejected here rather than silently forwarded to ansible-playbook; pass
    # ansible args after `--`.
    local _takes_target=false
    [[ "$ACTION" =~ ^(install|teardown|validate)$ ]] && _takes_target=true

    # configure and show read neither env nor target, so an option there is a mistake
    # rather than a no-op.
    [[ "$ACTION" =~ ^(configure|show)$ && $# -gt 0 ]] \
        && err "'${ACTION}' takes no arguments (got '$1')."

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --env)           _opt_value --env "${2:-}"; ENV_NAME="$2"; shift 2 ;;
            --only)          [[ "$_takes_target" == "true" ]] \
                                 || err "--only applies to install/teardown/validate only."
                             ONLY=true; shift ;;
            --force)         FORCE=true; shift ;;
            --skip)          _opt_value --skip "${2:-}"; SKIP="$2"; shift 2 ;;
            --dry-run)       [[ "$ACTION" =~ ^(add-node|remove-node)$ ]] \
                                 || err "--dry-run applies to add-node/remove-node only."
                             DRY_RUN=true; shift ;;
            --)              shift; EXTRA_VARS+=("$@"); break ;;
            -*)              err "Unknown option: '$1'. Valid: --env <name> | --only | --force | --skip <names> | --dry-run (pass ansible args after --). Try --help." ;;
            *)
                if [[ "$_takes_target" == "true" && -z "$TARGET" ]]; then
                    TARGET="$1"; shift
                else
                    err "Unexpected argument: '$1'. Try --help."
                fi
                ;;
        esac
    done

    if [[ "$_takes_target" == "true" && -z "$TARGET" ]]; then
        # show_table needs yq and errs without it, which would replace this message with a
        # less useful one, so say it as an error rather than a warning.
        command -v yq &>/dev/null || err "${ACTION} needs a target: <layer> | <component>."
        warn "Specify a target: <layer> | <component>"
        show_table; exit 1
    fi
}

# main: linear flow, no branching on cluster/mode/storage state.
# _target_exists <name> — a layer or a component in the merged registry.
# Here-string for the same reason as _layer_exists.
_target_exists() {
    local names; names=$(_merged_components | yq -r '.layers[].name, .components[].name' 2>/dev/null)
    grep -qxF -- "$1" <<< "$names"
}

# _resolve_kubeconfig — set KUBE_CFG and IS_BYO. global_config.yaml declaring
# existing_kubernetes means bring-your-own cluster; otherwise the env manages its own.
_resolve_kubeconfig() {
    local byo=""
    if [[ -f "$GLOBAL_CONFIG" ]] && command -v yq &>/dev/null; then
        byo="$(yq -r '.existing_kubernetes // ""' "$GLOBAL_CONFIG" 2>/dev/null)"
    fi
    if [[ -n "$byo" ]]; then
        byo="${byo/#\~/$HOME}"   # yq returns a literal ~, which no k8s client expands
        [[ -r "$byo" ]] \
            || err "existing_kubernetes is '${byo}', which does not exist or is not readable."
        IS_BYO=true
        KUBE_CFG="$byo"
        info "BYO cluster mode — kubeconfig: ${KUBE_CFG}"
    else
        IS_BYO=false
        KUBE_CFG="${ENV_DIR}/kubeconfig.yaml"
    fi
}

# _needs_kubespray — true when this run has to drive the kubespray bash bridge.
#
# Kubespray cannot run inside Ansible (ansible-in-ansible swallows all progress), so bash
# drives it: prep → cluster.yml/reset.yml → post. A BYO cluster never needs it.
#   install:  kubernetes is required for any target, since everything depends on
#             infrastructure transitively — unless --only narrows away from it.
#   teardown: the cluster is destroyed ONLY when explicitly targeted. Tearing down a
#             higher layer must not reset it.
_needs_kubespray() {
    [[ "$IS_BYO" == "false" ]] || return 1
    case "$ACTION" in
        teardown) [[ "$TARGET" == "infrastructure" || "$TARGET" == "kubernetes" ]] ;;
        *)        [[ "$ONLY" != "true" ]] \
                      || [[ "$TARGET" == "kubernetes" || "$TARGET" == "infrastructure" ]] ;;
    esac
}

# _completion_banner — single terminal point for install/teardown/validate.
#
# Deliberately not in site.yaml: `run site` fires twice on a cold install and three times
# on teardown via the kubespray bridge, so a playbook-side banner would print once per pass.
# validate lands here too, so the wording cannot hardcode install/teardown.
_completion_banner() {
    local headline
    case "$ACTION" in
        install)  headline="INSTALLATION COMPLETE" ;;
        teardown) headline="TEARDOWN COMPLETE" ;;
        *)        headline="${ACTION^^} COMPLETE" ;;
    esac
    # An action with no target would read "Target:" with nothing after it.
    # Omit the line rather than inventing a value.
    local -a target_line=()
    [[ -z "$TARGET" ]] || target_line=("Target:  ${TARGET}")
    banner "$headline" \
           "" \
           "${target_line[@]}" \
           "Env:     ${ENV_NAME}" \
           "Log:     ${LOG}"
}

main() {
    parse_args "$@"

    case "$ACTION" in
        configure)   configure;        exit 0 ;;
        init)        init;             exit 0 ;;
        show)        show_table;       exit 0 ;;
    esac

    info "${ACTION^}: ${TARGET:-cluster}  (env=${ENV_NAME}, installer ${INSTALLER_VERSION})"

    ensure_env_dir   "$ENV_NAME"
    ensure_repos "$TARGET"
    # After ensure_repos, so ext/ components are discoverable; before the venv and sudo
    # work, which a typo should not have to pay for.
    [[ -z "$TARGET" ]] || _target_exists "$TARGET" \
        || err "Unknown target '${TARGET}'. Layers: $(known_layers | tr '\n' ' ')— run './$(basename "$0") show' for components."
    # A solution layer (one with a manifest) must also be one this env was inited for.
    if [[ -n "$(layer_manifest "$TARGET")" ]] \
       && ! grep -qxF -- "$TARGET" <<< "$(solution_layers)"; then
        err "env/${ENV_NAME} was not provisioned for layer '${TARGET}'. Run:  ./$(basename "$0") init ${TARGET} --env ${ENV_NAME}"
    fi
    ensure_deps
    ensure_sudo
    resolve_inventory

    # TARGET is empty for `status`; fall back so the filename has no
    # double dash (status--<ts>.log).
    LOG="${ENV_LOG_DIR}/${ACTION}-${TARGET:-cluster}-$(date +%Y%m%d-%H%M%S).log"
    # Keep the 30 most recent; a failed run's log is the only record of why.
    ls -1t "$ENV_LOG_DIR"/*.log 2>/dev/null | tail -n +31 | xargs -r rm -f || true

    local -a vars=(
        -e "_skip_components=${SKIP}"
        -e "component_action=${ACTION}"
        -e "target=${TARGET}"
        -e "env_name=${ENV_NAME}"
        -e "_include_deps=$([[ "$ONLY" == "true" ]] && echo false || echo true)"
        -e "env_dir=${ENV_DIR}"
        -e "kubespray_dir=${SCRIPT_DIR}/.kubespray"
        -e "kubespray_custom_inventory=${INVENTORY}"
    )
    # Solution configs, in the order the provisioning record lists them (dependencies
    # first, so a layer's own config wins over its deps'). Collected before the loop:
    # solution_configs errors on a config the record names but that is gone, and inside
    # a process substitution that exit would be swallowed.
    if command -v yq &>/dev/null; then
        local _sol_cfgs; _sol_cfgs=$(solution_configs)
        local _env_cfg
        while IFS= read -r _env_cfg; do
            [[ -n "$_env_cfg" ]] && vars+=(-e "@$_env_cfg")
        done <<< "$_sol_cfgs"
        vars+=(-e "_env_layers=$(solution_layers | paste -sd, -)")
    fi
    [[ -f "$GLOBAL_CONFIG" ]] && vars+=(-e "@$GLOBAL_CONFIG")
    # Only load nodes.yaml if it has actual YAML content (all-comments = null → Ansible rejects it)
    if [[ -f "$ENV_DIR/nodes.yaml" ]] && [[ "$(yq -r 'type' "$ENV_DIR/nodes.yaml" 2>/dev/null)" == "!!map" ]]; then
        vars+=(-e "@$ENV_DIR/nodes.yaml")
    fi

    _resolve_kubeconfig
    vars+=(-e "kubernetes_kubeconfig=${KUBE_CFG}")
    [[ "$IS_BYO" == "true" ]] && vars+=(-e "existing_kubernetes=${KUBE_CFG}")
    # Export after the path is resolved so Ansible collections and kubectl
    # both see the correct kubeconfig. Kubespray's subshell unsets these (see
    # run_kubespray) so kubespray's own internal kubectl is unaffected.
    export KUBECONFIG="$KUBE_CFG" K8S_AUTH_KUBECONFIG="$KUBE_CFG"

    # Status: reuses all the setup above, runs the status playbook, then exits.
    if [[ "$ACTION" == "status" ]]; then
        if [[ ! -f "$KUBE_CFG" ]]; then
            echo -e "\n${YEL}No kubeconfig found at ${KUBE_CFG} — nothing is installed yet.${RST}"
            exit 0
        fi
        run status "${vars[@]}" "${EXTRA_VARS[@]}"
        exit 0
    fi

    # add-node / remove-node: every pass is scoped to the kubernetes component
    # only — these actions never touch anything else. No node name is taken on
    # the CLI; do_add_node/do_remove_node diff nodes.yaml against the live
    # cluster to find it (see their definitions above).
    if [[ "$ACTION" == "add-node" || "$ACTION" == "remove-node" ]]; then
        [[ -f "$KUBE_CFG" ]] \
            || err "No kubeconfig at ${KUBE_CFG} — install the cluster first:  ./$(basename "$0") install --env ${ENV_NAME} kubernetes"
        [[ "$IS_BYO" == "false" ]] \
            || err "${ACTION} is not supported for a BYO cluster (existing_kubernetes is set in global_config.yaml)."
        TARGET="kubernetes"
        vars+=(-e "target=kubernetes")
        case "$ACTION" in
            add-node)    do_add_node ;;
            remove-node) do_remove_node ;;
        esac
        _completion_banner
        exit 0
    fi

    local _includes_k8s=false
    _needs_kubespray && _includes_k8s=true

    if [[ "$ACTION" == "teardown" ]]; then
        local _what="'${TARGET}' and everything above it, in env/${ENV_NAME}"
        [[ "$_includes_k8s" == "true" ]] \
            && _what="${_what}
  This DESTROYS the Kubernetes cluster: kubespray reset runs with reset_confirmation=yes."
        confirm "About to tear down ${_what}"
    fi

    # When the kubeconfig already exists, a plain single pass is enough: the
    # kubernetes role probes the cluster (kubectl cluster-info) and self-skips
    # via kubernetes_skip_if_exists. Kubespray only runs to provision/destroy.
    if [[ "$_includes_k8s" == "true" && "$ACTION" == "install" && ! -f "$KUBE_CFG" ]]; then
        run site "${vars[@]}" -e "target=kubernetes" "${EXTRA_VARS[@]}"
        run_kubespray install
        run site "${vars[@]}" -e "_kubespray_skip_cluster_yml=true" "${EXTRA_VARS[@]}"
    elif [[ "$_includes_k8s" == "true" && "$ACTION" == "teardown" ]]; then
        # Tear down non-kubernetes components first (need cluster alive), then reset.
        if [[ "$TARGET" != "kubernetes" ]]; then
            # Merged, not replaced: this pass must still leave kubernetes for the reset.
            run site "${vars[@]}" -e "_skip_components=kubernetes${SKIP:+,${SKIP}}" \
                -e "component_action=teardown" "${EXTRA_VARS[@]}"
        fi
        run site "${vars[@]}" -e "target=kubernetes" -e "_kubespray_prep_only=true" -e "_include_deps=false" "${EXTRA_VARS[@]}"
        run_kubespray teardown
        run site "${vars[@]}" -e "target=kubernetes" -e "component_action=teardown" -e "_include_deps=false" "${EXTRA_VARS[@]}"
        [[ -f "${ENV_DIR}/kubeconfig.yaml" ]] && { rm -f "${ENV_DIR}/kubeconfig.yaml"; ok "Removed kubeconfig"; }
    else
        # No kubespray involved — single pass
        run site "${vars[@]}" "${EXTRA_VARS[@]}"
    fi

    _completion_banner
}

main "$@"
