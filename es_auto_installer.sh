#!/usr/bin/env bash
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION=$(cat "${SCRIPT_DIR}/VERSION" 2>/dev/null || echo dev)

CONFIG_DIR="${SCRIPT_DIR}/config"
COMPONENTS="${CONFIG_DIR}/components.yaml"
REPOS_CONFIG="${CONFIG_DIR}/repos.yaml"
DEFAULTS_DIR="${CONFIG_DIR}/defaults"

EXT_DIR="${SCRIPT_DIR}/ext"
VENV="${SCRIPT_DIR}/.venv"
ENV_ROOT="${SCRIPT_DIR}/env"

YQ_VERSION="v4.53.2"
KUBECTL_VERSION="v1.34.3"
HELM_VERSION="v3.20.2"

# Valid CLI actions.
ACTIONS=(configure show init install teardown validate status)

# Logging helper variables and functions
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
        "$@" &>/dev/null &
        local _pid=$!
        while kill -0 "$_pid" 2>/dev/null; do
            sleep 2
            kill -0 "$_pid" 2>/dev/null && printf '.'
        done
        local _rc=0; wait "$_pid" || _rc=$?
        printf '\n'
        return "$_rc"
    fi
}

usage() {
    local me; me=$(basename "$0")
    cat <<EOF

  Usage:  $me <action> [args] [-- ansible-playbook args]

  Actions:
    configure              install Python ≥ 3.11 + venv pkg + yq + kubectl + helm
                           (sudo, PERMANENT system changes). Run once per machine;
                           skip if those are already present.
    init <env>             create env/<env>/ and seed configs from core + ext
                           defaults. See README.md for configuration guidance.
    show                   print available layers/components
    install   <target>     provision a layer or component into env/<env>/
    teardown  <target>     remove a layer or component
    validate  <target>     run validate.yaml against current state
    status                 show what is currently installed (namespaces, pods,
                           helm releases, endpoints)

  Targets:
    --all                  every enabled layer, install order
    <layer>                single layer (Ansible routes it to its components)
    <component>            single component (Ansible routes it to its layer)

  Options:
    --env <name>           env directory under env/<name>/ (default: local)
    --only                 skip dep auto-pull; run target alone
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
    $me init local                             # seed env/local/ from defaults
    $me install --all                          # full stack into env=local
    $me status                                 # see what's installed (env=local)
    $me status --env prod                      # check prod environment
    $me init prod
    $me install --env prod platform            # multi-env on one bastion
    $me install metallb --only -- -vvv

EOF
}

# Print the merged components.yaml from core + every ext repo
_merged_components() {
    local files=("$COMPONENTS")
    [[ -d "$EXT_DIR" ]] && while IFS= read -r -d '' f; do files+=("$f"); done < \
        <(find -L "$EXT_DIR" -name components.yaml -type f \
                -not -path '*/\.git/*' -print0 2>/dev/null | sort -z)
    # Surface a malformed ext components.yaml instead of emitting an empty table.
    yq eval-all '. as $i ireduce ({}; . *+ $i)' "${files[@]}" \
        || warn "failed to merge components.yaml (malformed ext file? files: ${files[*]})"
}

# _discover_layers — populate LAYERS from the MERGED components (core + ext), not
# core alone, so an ext-only layer still renders. Lazy: called after yq exists.
LAYERS=()
_discover_layers() {
    mapfile -t LAYERS < <(_merged_components | yq -r '.layers[].name' 2>/dev/null)
}

show_table() {
    command -v yq >/dev/null || err "yq not found — run './es_auto_installer.sh configure' or install yq"

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
    read -rp "    Proceed? [y/N] " _ans
    [[ "$_ans" =~ ^[Yy]$ ]] || { info "Aborted."; exit 0; }

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
    info "Next:  ./es_auto_installer.sh init local  (see README.md for details)"
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
    # The commas are sudo's own --preserve-env list syntax, not array separators.
    # shellcheck disable=SC2054
    [[ "$use_sudo" == "--sudo" ]] && pre=(sudo --preserve-env=http_proxy,https_proxy,no_proxy)

    command -v wget &>/dev/null \
        || err "wget is required for downloads but is not installed."

    local -a q=(-q); (( _LOG_LEVEL >= 1 )) && q=(--progress=bar:force:noscroll)
    "${pre[@]}" wget "${q[@]}" --connect-timeout=15 --read-timeout=60 --tries=3 \
        -O "$dest" "$url" && return 0

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

# ensure_repos [solution...] — clone repos and build ANSIBLE_ROLES_PATH.
# Repos with always:true are always cloned. Optional repos are cloned only when
# their solution tag is passed (i.e. during init --<solution>). Already-cloned
# repos are always added to the roles path regardless of arguments.
ensure_repos() {
    local -a clone_solutions=("$@")
    local roles="${SCRIPT_DIR}/roles"
    if [[ -f "$REPOS_CONFIG" ]] && command -v yq &>/dev/null; then
        local url dest branch subdir always solution repo roles_dir
        while IFS='|' read -r url dest branch subdir always solution; do
            [[ -z "$url" || -z "$dest" ]] && continue
            repo="${EXT_DIR}/${dest}"
            roles_dir="${repo}${subdir:+/$subdir}/roles"
            if [[ ! -d "$roles_dir" ]]; then
                local _should_clone=false
                if [[ "$always" == "true" ]]; then
                    _should_clone=true
                else
                    local _s
                    for _s in "${clone_solutions[@]:-}"; do [[ "$_s" == "$solution" ]] && { _should_clone=true; break; }; done
                fi
                [[ "$_should_clone" == "true" ]] || continue
                command -v git &>/dev/null \
                    || err "git is required to clone ${dest} but is not installed. Install git or run './es_auto_installer.sh configure'."
                mkdir -p "$EXT_DIR"
                info "Cloning ${dest} (${branch})"
                if [[ "$branch" == refs/* ]]; then
                    # A full ref (e.g. refs/pull/N/head) cannot be given to
                    # `git clone --branch`: the default fetch refspec never retrieves
                    # refs/pull/*. Clone the default branch, then fetch the ref and
                    # detach onto it so the tree is on the requested commit before the
                    # env configs below are seeded from it.
                    { git clone "$url" "$repo" \
                        && git -C "$repo" fetch --depth 1 origin "$branch" \
                        && git -C "$repo" checkout --detach FETCH_HEAD; } \
                        || { rm -rf "$repo"; err "git clone/checkout of ${branch} failed for ${url}. Check the ref exists, plus credentials, network, and proxy."; }
                else
                    git clone --branch "$branch" "$url" "$repo" \
                        || { rm -rf "$repo"; git clone "$url" "$repo"; } \
                        || err "git clone failed for ${url}. Check credentials, network, and proxy."
                fi
            fi
            if [[ -d "$roles_dir" ]]; then
                roles="${roles}:${roles_dir}"
            else
                warn "${dest} has no roles/ at ${roles_dir#${SCRIPT_DIR}/} — check deployment_subdir in repos.yaml; ansible will not find its roles."
            fi
        done < <(yq -r '.repos[] | [.url, .dest, (.branch // "main"), (.deployment_subdir // ""), (.always | tostring), (.solution // "")] | join("|")' "$REPOS_CONFIG" 2>/dev/null)
    fi
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
            err "env/${name} does not exist. Run:  ./es_auto_installer.sh init ${name}"
        fi
    fi
    ENV_LOG_DIR="${ENV_DIR}/logs"
    ENV_INVENTORY_DIR="${ENV_DIR}/inventory"
    GLOBAL_CONFIG="${ENV_DIR}/global_config.yaml"
    mkdir -p "$ENV_LOG_DIR" "$ENV_INVENTORY_DIR"
}

resolve_inventory() {
    local f="${ENV_INVENTORY_DIR}/hosts.yaml"
    [[ -f "$f" ]] || err "Inventory missing: ${f}. Run: ./es_auto_installer.sh init ${ENV_NAME}"
    INVENTORY="$f"
    info "Inventory: $INVENTORY"
}

# init: create env/<name>, generate localhost inventory, and seed defaults.
# Usage: init <env> [--rag] [--<solution>...]
# Repos with always:true always have their config seeded. Optional solutions
# (always:false) are seeded only when --<solution> is passed. The presence of
# env/<name>/config.<sol>.yaml is what marks a solution active at install time.
init() {
    local name="${1:-local}"
    shift || true

    # Parse --<solution> flags, validated against the solution tags in repos.yaml
    # so a typo (--rga) fails loudly instead of silently seeding nothing.
    local -a _valid_sols=()
    if [[ -f "$REPOS_CONFIG" ]] && command -v yq &>/dev/null; then
        mapfile -t _valid_sols < <(yq -r '.repos[].solution // "" | select(. != "")' "$REPOS_CONFIG" 2>/dev/null)
    fi
    local -a solutions=()
    local _sol_ok _vs
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --*)
                _sol_ok=false
                for _vs in "${_valid_sols[@]:-}"; do [[ "${1#--}" == "$_vs" ]] && { _sol_ok=true; break; }; done
                [[ "$_sol_ok" == "true" ]] \
                    || err "Unknown solution: '$1'. Valid: ${_valid_sols[*]:-<none in repos.yaml>} (each maps to a 'solution' tag in config/repos.yaml)."
                solutions+=("${1#--}"); shift
                ;;
            *)   shift ;;
        esac
    done

    ENV_DIR="${ENV_ROOT}/${name}"
    info "init: env=${name}${solutions:+  solutions: ${solutions[*]}}  →  ${ENV_DIR}"
    mkdir -p "$ENV_DIR/inventory" "$ENV_DIR/logs"

    local inv_file="$ENV_DIR/inventory/hosts.yaml"
    if [[ ! -f "$inv_file" ]]; then
        cp "${SCRIPT_DIR}/inventory/hosts.yaml" "$inv_file"
        info "  created: ${name}/inventory/hosts.yaml"
    fi

    command -v yq &>/dev/null || err "yq missing. Run './es_auto_installer.sh configure' first."
    ensure_repos "${solutions[@]+"${solutions[@]}"}"

    # Seed core defaults
    local f base
    for f in "$DEFAULTS_DIR"/*.yaml; do
        [[ -e "$f" ]] || continue
        base=$(basename "$f")
        if [[ -f "$ENV_DIR/$base" ]]; then
            warn "  exists, skipping: $base"
        else
            cp "$f" "$ENV_DIR/$base"
            info "  created: ${name}/${base}"
        fi
    done

    # Seed ext repo configs based on always:true or matching --<solution>
    local _sol _always _dest _subdir _cfg _s
    while IFS='|' read -r _dest _subdir _sol _always; do
        [[ -z "$_dest" ]] && continue
        if [[ "$_always" != "true" ]]; then
            local _wanted=false
            for _s in "${solutions[@]:-}"; do [[ "$_s" == "$_sol" ]] && { _wanted=true; break; }; done
            [[ "$_wanted" == "true" ]] || continue
        fi
        _cfg="${EXT_DIR}/${_dest}${_subdir:+/$_subdir}/config.yaml"
        if [[ -f "$_cfg" ]]; then
            local _target="$ENV_DIR/config.${_sol}.yaml"
            if [[ -f "$_target" ]]; then
                warn "  exists, skipping: config.${_sol}.yaml"
            else
                cp "$_cfg" "$_target"
                info "  created: ${name}/config.${_sol}.yaml  (from ext/${_dest})"
            fi
        fi
    done < <(yq -r '.repos[] | [.dest, (.deployment_subdir // ""), (.solution // ""), (.always | tostring)] | join("|")' "$REPOS_CONFIG" 2>/dev/null)

    # Seed model-manager models.yaml from the repo's model catalog
    local _mm_source="${EXT_DIR}/enterprise.ai-inference/model_manager/models.yaml"
    local _mm_target="${ENV_DIR}/models.yaml"
    if [[ -f "$_mm_source" ]]; then
        if [[ -f "$_mm_target" ]]; then
            warn "  exists, skipping: models.yaml"
        else
            cp "$_mm_source" "$_mm_target"
            info "  created: ${name}/models.yaml  (model catalog — edit to add/remove models)"
        fi
    fi

    ok "init: done."
    info "  Configure  ${ENV_DIR}/global_config.yaml  (see README.md § Configuration Reference)"
    info "  Configure  ${ENV_DIR}/models.yaml          (see README.md § Deploy a Model)"
    info "  Then       ./$(basename "$0") install --env ${name} --all"
}

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

# run_kubespray: execute kubespray cluster.yml directly from bash so
# the operator gets live streaming output. Ansible-in-ansible swallows
# all progress, so the kubernetes role only does prep; this function
# bridges prep → cluster.yml → post-kubespray.
run_kubespray() {
    local action="${1:-install}"
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

    local pb="$ks_dir/cluster.yml" extra=()
    [[ "$action" == "teardown" ]] && { pb="$ks_dir/reset.yml"; extra=(-e reset_confirmation=yes); }
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

# parse_args
parse_args() {
    ACTION="" TARGET="" ENV_NAME="local" ONLY=false
    INIT_ARG=""
    INIT_EXTRA=()
    EXTRA_VARS=()

    [[ $# -eq 0 || "${1:-}" =~ ^(-h|--help)$ ]] && { usage; exit 0; }
    [[ "$1" =~ ^(-v|--version)$ ]] && { echo "es_auto_installer ${VERSION}"; exit 0; }

    ACTION="$1"; shift

    # Guard: action must be one of the known verbs.
    local _known=false _a
    for _a in "${ACTIONS[@]}"; do [[ "$ACTION" == "$_a" ]] && { _known=true; break; }; done
    [[ "$_known" == "true" ]] || err "Unknown action: '$ACTION'. Valid: ${ACTIONS[*]} (try --help)"

    # init keeps its own positional-then-solution-flags grammar (see init()).
    if [[ "$ACTION" == "init" ]]; then
        [[ $# -gt 0 && "$1" != -* ]] && { INIT_ARG="$1"; shift; }
        INIT_EXTRA=("$@"); set --
    fi

    # Unified parse for the remaining verbs. The target may appear anywhere (not
    # just first), so `install --env prod platform` works — the first bare
    # (non-dash) token becomes the target regardless of position. Unknown --flags
    # are rejected here rather than silently forwarded to ansible-playbook; pass
    # ansible args after `--`.
    local _takes_target=false
    [[ "$ACTION" =~ ^(install|teardown|validate)$ ]] && _takes_target=true

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --env)           ENV_NAME="$2"; shift 2 ;;
            --only)          ONLY=true; shift ;;
            --all)           TARGET=all; shift ;;
            --)              shift; EXTRA_VARS+=("$@"); break ;;
            -*)              err "Unknown option: '$1'. Valid: --env <name> | --only | --all (pass ansible args after --). Try --help." ;;
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
        warn "Specify a target: --all | <layer> | <component>"
        show_table; exit 1
    fi
}

# main: linear flow, no branching on cluster/mode/storage state.
main() {
    parse_args "$@"

    case "$ACTION" in
        configure)   configure;        exit 0 ;;
        init)        init "$INIT_ARG" "${INIT_EXTRA[@]+"${INIT_EXTRA[@]}"}"; exit 0 ;;
        show)        show_table;       exit 0 ;;
    esac

    info "${ACTION^}: ${TARGET:-all}  (env=${ENV_NAME}, installer v${VERSION})"

    ensure_env_dir   "$ENV_NAME"
    ensure_repos
    ensure_deps
    ensure_sudo
    resolve_inventory

    # TARGET is empty for `status`; fall back to "all" so the filename has no
    # double dash (status--<ts>.log).
    LOG="${ENV_LOG_DIR}/${ACTION}-${TARGET:-all}-$(date +%Y%m%d-%H%M%S).log"

    local -a vars=(
        -e "component_action=${ACTION}"
        -e "target=${TARGET}"
        -e "env_name=${ENV_NAME}"
        -e "_include_deps=$([[ "$ONLY" == "true" ]] && echo false || echo true)"
        -e "env_dir=${ENV_DIR}"
        -e "kubespray_dir=${SCRIPT_DIR}/.kubespray"
        -e "kubespray_custom_inventory=${INVENTORY}"
    )
    # Load solution configs from the env only. init seeds env/<name>/config.<sol>.yaml
    # from the ext repo baseline; install consumes that editable copy
    if [[ -f "$REPOS_CONFIG" ]] && command -v yq &>/dev/null; then
        local _sol _env_cfg
        while IFS= read -r _sol; do
            [[ -z "$_sol" ]] && continue
            _env_cfg="${ENV_DIR}/config.${_sol}.yaml"
            [[ -f "$_env_cfg" ]] && vars+=(-e "@$_env_cfg")
        done < <(yq -r '.repos[].solution // ""' "$REPOS_CONFIG" 2>/dev/null)
    fi
    [[ -f "$GLOBAL_CONFIG" ]] && vars+=(-e "@$GLOBAL_CONFIG")
    # Only load nodes.yaml if it has actual YAML content (all-comments = null → Ansible rejects it)
    if [[ -f "$ENV_DIR/nodes.yaml" ]] && [[ "$(yq -r 'type' "$ENV_DIR/nodes.yaml" 2>/dev/null)" == "!!map" ]]; then
        vars+=(-e "@$ENV_DIR/nodes.yaml")
    fi

    # Resolve kubeconfig path. If global_config.yaml declares existing_kubernetes,
    # use that (BYO cluster mode). Otherwise default to the env-managed path.
    local _byo_kc=""
    if [[ -f "$GLOBAL_CONFIG" ]] && command -v yq &>/dev/null; then
        _byo_kc="$(yq -r '.existing_kubernetes // ""' "$GLOBAL_CONFIG" 2>/dev/null)"
    fi
    local _is_byo=false
    local KUBE_CFG
    if [[ -n "$_byo_kc" ]]; then
        _is_byo=true
        KUBE_CFG="$_byo_kc"
        info "BYO cluster mode — kubeconfig: ${KUBE_CFG}"
    else
        KUBE_CFG="${ENV_DIR}/kubeconfig.yaml"
    fi
    vars+=(-e "kubernetes_kubeconfig=${KUBE_CFG}")
    [[ -n "$_byo_kc" ]] && vars+=(-e "existing_kubernetes=${_byo_kc}")
    # Export after the path is resolved so Ansible collections and kubectl
    # both see the correct kubeconfig. Kubespray's subshell unsets these (see
    # run_kubespray) so kubespray's own internal kubectl is unaffected.
    export KUBECONFIG="$KUBE_CFG" K8S_AUTH_KUBECONFIG="$KUBE_CFG"

    # Status: reuses all the setup above, runs the status playbook, then exits.
    if [[ "$ACTION" == "status" ]]; then
        run status "${vars[@]}" "${EXTRA_VARS[@]}"
        exit 0
    fi

    # Kubespray orchestration: does this target need the kubespray bash bridge?
    # Kubespray can't run inside Ansible (ansible-in-ansible swallows progress),
    # so bash drives it: prep → cluster.yml/reset.yml → post. BYO skips it.
    # Install: kubernetes is needed for any target (all depend on infra
    #          transitively) unless --only narrows away from it.
    # Teardown: the cluster is destroyed ONLY when explicitly targeting it, its
    #           layer, or --all. Tearing down a higher layer must NOT reset it.
    local _includes_k8s=false
    if [[ "$_is_byo" == "false" ]]; then
        if [[ "$ACTION" == "teardown" ]]; then
            [[ "$TARGET" == "all" || "$TARGET" == "infrastructure" || "$TARGET" == "kubernetes" ]] && _includes_k8s=true
        elif [[ "$ONLY" == "true" ]]; then
            [[ "$TARGET" == "kubernetes" || "$TARGET" == "infrastructure" ]] && _includes_k8s=true
        else
            _includes_k8s=true
        fi
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
            run site "${vars[@]}" -e "_skip_components=kubernetes" -e "component_action=teardown" "${EXTRA_VARS[@]}"
        fi
        run site "${vars[@]}" -e "target=kubernetes" -e "_kubespray_prep_only=true" -e "_include_deps=false" "${EXTRA_VARS[@]}"
        run_kubespray teardown
        run site "${vars[@]}" -e "target=kubernetes" -e "component_action=teardown" -e "_include_deps=false" "${EXTRA_VARS[@]}"
        [[ -f "${ENV_DIR}/kubeconfig.yaml" ]] && { rm -f "${ENV_DIR}/kubeconfig.yaml"; ok "Removed kubeconfig"; }
    else
        # No kubespray involved — single pass
        run site "${vars[@]}" "${EXTRA_VARS[@]}"
    fi

    # Single terminal point for install/teardown. Deliberately NOT in site.yaml:
    # `run site` fires up to 3 times on install and 4 on teardown (kubespray
    # bridge above), so a playbook-side banner would print once per pass.
    # `validate` lands here too, so don't hardcode install/teardown wording.
    local _headline
    case "$ACTION" in
        install)  _headline="INSTALLATION COMPLETE" ;;
        teardown) _headline="TEARDOWN COMPLETE" ;;
        *)        _headline="${ACTION^^} COMPLETE" ;;
    esac
    banner "$_headline" \
            "" \
            "Target:  ${TARGET}" \
            "Env:     ${ENV_NAME}" \
            "Log:     ${LOG}"
}

main "$@"
