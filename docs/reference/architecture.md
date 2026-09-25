# Architecture & Design

[← Docs Index](../README.md)

## Overview

Intel® AI for Enterprise Solutions is a modular, extensible deployment framework for a self-hosted Kubernetes cluster, LLM inference serving stack, and opt-in applications — deployable on-premises, air-gapped, or in a private cloud.

This repository provides:
- **Infrastructure layer** - Kubernetes (via Kubespray), storage (local-path, NFS, Ceph or NetApp ONTAP via Trident)
- **Platform layer** — Cert-manager, Istio (ambient), MetalLB, Envoy Gateway, PostgreSQL, Keycloak, Object Store (SeaweedFS), Observability
- **Multi-environment support** — Isolated configs under `env/<name>/`
- **Cross-repo orchestration** — Auto-discovers and integrates external solution repos

External solution repositories (e.g., `enterprise-inference`, `enterprise-rag`) contribute additional layers:
- **Inference layer** — KServe, LiteLLM (proxy + auth), Langfuse (observability), LLM services, Envoy AI Gateway, NRI CPU balloons
- **Application layer** — RAG pipelines, UI, vector databases (opt-in)

**Auth modes:** The stack supports two authentication providers (set via `auth_provider` in `global_config.yaml`):
- `litellm` — LiteLLM virtual keys; Keycloak is not deployed; LiteLLM acts as both auth (extAuth `/key/verify`) and model proxy
- `keycloak` — Keycloak OIDC JWT; LiteLLM/Langfuse are not deployed; direct KServe path-based routing

---

## Layer Architecture

Intel® AI for Enterprise Solutions installs in four ordered layers, each depending on the one before it:

```
infrastructure  →  platform  →  inference  →  erag (opt-in)
      │               │              │               │
  kubernetes      cert_manager    envoy_ai_gw    app_inference_models
  storage         istio           kserve         app_pre_install
                  metallb         litellm        app_vector_databases
                  envoy_gateway   langfuse       app_keycloak_config
                  postgresql      keycloak_cfg   app_apisix
                  keycloak*       llm_services   app_chat_history
                  object_store    nri_cpu_balloons  app_nats
                  seaweedfs                      app_fingerprint
                  observability                  app_hpa
                  velero†                        app_pipeline
                                                 app_edp
                                                 app_mcp_gateway
                                                 app_ui
                                                 app_watcher
                                                 app_post_install

* keycloak is auto-disabled when auth_provider=litellm
† velero is opt-in (velero_enabled) and needs snapshot-capable storage
```

- **`install inference`** installs infrastructure + platform + inference (dependencies auto-pull)
- **`install erag`** (and future layers) must be explicitly targeted
- Dependencies are auto-resolved from `components.yaml` definitions

---

## Execution Flow

A single `es_auto_installer.sh install` invocation is how Intel® AI for Enterprise Solutions resolves components and dispatches Ansible for every layer:

```
./es_auto_installer.sh install inference --env local
│
├─ 1. Load environment: env/<name>/global_config.yaml + config.<solution>.yaml
├─ 2. Resolve components: load components.yaml from this repo + ext repos → build active component list
├─ 3. Preflight checks (internet connectivity for non-teardown/validate actions)
├─ 4. Dispatch to playbooks/site.yaml with component_action + target
│
playbooks/site.yaml (universal dispatcher)
│
├─ includes/preflight.yaml:
│   ├─ Load component registry (configs/components.yaml + ext components.yaml)
│   ├─ Resolve component set from target + dependencies
│   └─ Verify kubectl connectivity
│   (solution + global configs are passed as -e @ by the installer)
│
├─ [Infrastructure Layer]
│   └─ role: kubernetes          → tasks/{{ component_action }}.yaml
│         ├─ Check for existing cluster (kubectl cluster-info)
│         │   ├─ If existing_kubernetes is set (BYO) → skip install
│         │   ├─ If exists + version OK → skip install
│         │   └─ If not exists ↓
│         ├─ precheck.yaml        (swap, kernel modules, sysctl, packages)
│         ├─ install_kubespray.yaml (prep phase):
│         │   ├─ Clone kubespray repo to .kubespray/ (pinned version)
│         │   ├─ Create python venv + install requirements.txt
│         │   ├─ Read inventory from env/<name>/inventory/hosts.yaml
│         │   ├─ Apply kubespray_user_overrides or kubespray_full_config_file
│         │   └─ Exit (3-phase split) → installer runs kubespray/cluster.yml
│         ├─ post_install.yaml    (kubeconfig copy, node wait, helm verify)
│         └─ storage, metallb (infrastructure components)
│
├─ [Platform Layer]
│   ├─ role: cert_manager         → tasks/install.yaml (helm install)
│   ├─ role: istio                → tasks/install.yaml (ambient mesh — all workload namespaces enrolled)
│   ├─ role: metallb              → tasks/install.yaml (LoadBalancer services)
│   ├─ role: envoy_gateway        → tasks/install.yaml (helm install + TLS)
│   ├─ role: postgresql           → tasks/install.yaml (CNPG operator + clusters)
│   ├─ role: keycloak             → tasks/install.yaml (skipped when auth_provider=litellm)
│   ├─ role: object_store         → tasks/install.yaml (backend selection)
│   ├─ role: seaweedfs            → tasks/install.yaml (S3-compatible object storage)
│   └─ role: observability        → tasks/install.yaml (prometheus/grafana/loki/tempo)
│
├─ [Inference Layer]
│   ├─ role: keycloak_config      → tasks/install.yaml (realm + client — skipped when auth_provider=litellm)
│   ├─ role: envoy_ai_gateway     → tasks/install.yaml (apply manifests)
│   ├─ role: kserve               → tasks/install.yaml (CRDs + controller + deploy mode)
│   ├─ role: litellm              → tasks/install.yaml (proxy + extAuth — only when auth_provider=litellm)
│   ├─ role: langfuse             → tasks/install.yaml (observability — only when auth_provider=litellm)
│   ├─ role: llm_services         → tasks/install.yaml (runtimes + default models)
│   └─ role: nri_cpu_balloons     → tasks/install.yaml (CPU pinning — only when cpu_policy=nri-balloons)
│
└─ [Intel AI for Enterprise RAG Layer] (opt-in, not included by `install inference`)
    └─ roles from ext/enterprise.ai-erag (15 components: app_inference_models, app_pre_install,
       app_vector_databases, app_keycloak_config, app_apisix, app_chat_history, app_nats,
       app_fingerprint, app_hpa, app_pipeline, app_edp, app_mcp_gateway, app_ui, app_watcher,
       app_post_install)
```

---

## Repository Structure

Intel® AI for Enterprise Solutions spans this repository plus external solution repositories that plug into it.

### enterprise-ai-solutions

```
enterprise-ai-solutions/
├── es_auto_installer.sh                        # CLI: configure, show, init, install, teardown, validate, status
├── ansible.cfg                                 # Ansible settings + dynamic roles_path
├── .kubespray/                                 # Kubespray clone
├── ext/                                        # External repos cloned here
│   └── enterprise.ai-inference/
│       └── config.yaml                         # Seed for init (copied to env/, not read at install)
├── env/                                        # Per-environment configs + state
│   └── local/                                  # Example environment
│       ├── global_config.yaml                  # Main config (components, TLS, proxy)
│       ├── inventory/
│       │   └── hosts.yaml                      # Kubespray-compatible inventory (YAML)
│       ├── config.erag.yaml                    # Solution-specific config (if init erag)
│       ├── config.inference.yaml               # Solution-specific config
│       ├── kubeconfig.yaml                     # Generated after cluster install
│       ├── nodes.yaml                          # Optional: MetalLB ranges, NFS overrides
│       └── logs/                               # Per-run logs
├── inventory/
│   └── hosts.yaml                              # Template (copied by init) + ansible.cfg default
├── playbooks/
│   ├── site.yaml                               # Universal dispatcher (all actions)
│   ├── includes/
│   │   ├── preflight.yaml                      # Component resolution + connectivity
│   │   └── run_component.yaml                  # Per-component dispatch loop
│   └── status.yaml                             # Cluster status reporting
└── roles/
    ├── kubernetes/
    │   ├── meta/main.yaml
    │   ├── defaults/main.yaml                  # K8s version, kubespray, networking
    │   ├── files/
    │   │   └── generate_reserved_cpus.sh       # NUMA detection script
    │   └── tasks/
    │       ├── main.yaml                       # Action dispatcher
    │       ├── install.yaml                    # Detect existing or install fresh
    │       ├── precheck.yaml                   # System prerequisites
    │       ├── install_kubespray.yaml          # Prep phase (3-phase split)
    │       ├── post_install.yaml               # Kubeconfig copy + health checks
    │       ├── teardown.yaml                   # Kubespray reset + cleanup
    │       └── validate.yaml                   # Health checks + version report
    ├── storage/                                # Storage engine (registry + resolver + meta-role)
    │   ├── defaults/main.yaml                  # storage_backend selector + storage_backends registry
    │   └── tasks/
    │       ├── resolve.yaml                    # selector → _resolved_storage_role (run in preflight)
    │       └── main.yaml                       # meta-role: include_role the resolved backend
    ├── nfs_storage/                            # Backend: NFS server + provisioner (RWX)
    │   └── tasks/
    │       ├── install.yaml
    │       └── teardown.yaml
    ├── rook_ceph_storage/                      # Backend: Ceph via Rook (multi-node block storage)
    │   └── tasks/
    │       ├── install.yaml
    │       └── teardown.yaml
    ├── netapp_trident_csi/                     # Backend: NetApp Trident + ONTAP (see docs/deploy/netapp_ontap.md)
    │   └── tasks/
    │       ├── validate_config.yaml            # Offline: variable-level checks, no cluster, no array
    │       ├── preflight_nodes.yaml            # Per-node: NFS client packages, mount helper, LIF names
    │       ├── preflight_ontap.yaml            # Array: TCP probes (ontap_preflight_online)
    │       ├── install.yaml
    │       ├── validate.yaml + validate_e2e.yaml
    ├── metallb/
    │   └── tasks/
    │       ├── install.yaml                    # MetalLB for LoadBalancer services
    │       └── teardown.yaml
    ├── cert_manager/
    │   └── tasks/
    │       ├── install.yaml                    # Helm install cert-manager
    │       └── teardown.yaml
    ├── istio/
    │   └── tasks/
    │       ├── install.yaml                    # Istio ambient mesh (ztunnel + waypoint)
    │       └── teardown.yaml
    ├── envoy_gateway/
    │   └── tasks/
    │       ├── install.yaml                    # Helm install + TLS config
    │       └── teardown.yaml
    ├── postgresql/
    │   └── tasks/
    │       ├── install.yaml                    # CNPG operator + database clusters
    │       └── teardown.yaml
    ├── keycloak/
    │   └── tasks/
    │       ├── install.yaml                    # Keycloak (skipped when auth_provider=litellm)
    │       └── teardown.yaml
    ├── object_store/
    │   └── tasks/
    │       └── install.yaml                    # Object store interface (seaweedfs)
    ├── seaweedfs/
    │   └── tasks/
    │       ├── install.yaml                    # S3-compatible object storage
    │       └── teardown.yaml
    ├── observability/
    │   └── tasks/
    │       ├── install.yaml                    # Prometheus + Grafana + Loki + Tempo
    │       └── teardown.yaml
    └── nri_cpu_balloons/
        └── tasks/
            ├── install.yaml                    # NRI plugin + per-node BalloonsPolicy CRs
            └── teardown.yaml
```

### enterprise-inference (external repo)

```
enterprise-inference/
├── config.yaml                                 # Seed for init (copied to env/config.inference.yaml)
├── model_manager/                              # Model lifecycle CLI
│   ├── model-manager                           # Entry point (resolves env/ models.yaml)
│   ├── models.yaml                             # Default model catalog (seeded to env/)
│   ├── default-models.yaml                     # Models deployed at initial install
│   └── scripts/                                # deploy, undeploy, litellm register, etc.
└── roles/
    ├── envoy_ai_gateway/
    │   └── tasks/
    │       ├── install.yaml                    # Envoy AI Gateway manifests
    │       └── teardown.yaml
    ├── keycloak_config/
    │   └── tasks/
    │       ├── install.yaml                    # Realm + client (skipped when auth_provider=litellm)
    │       └── teardown.yaml
    ├── kserve/
    │   ├── defaults/main.yaml
    │   └── tasks/
    │       ├── install.yaml                    # KServe CRDs + controller + deploy mode
    │       ├── teardown.yaml
    │       └── validate.yaml
    ├── litellm/
    │   ├── defaults/main.yaml                  # Version, sizing, cache, gateway integration
    │   └── tasks/
    │       ├── install.yaml                    # Helm deploy + extAuth SecurityPolicy + ReferenceGrant
    │       ├── tls.yaml                        # Certificate provisioning
    │       ├── teardown.yaml
    │       └── validate.yaml
    ├── langfuse/
    │   ├── defaults/main.yaml
    │   └── tasks/
    │       ├── install.yaml                    # Observability for LLM calls (traces, cost)
    │       └── teardown.yaml
    └── llm_services/
        ├── defaults/main.yaml
        ├── files/
        │   └── runtimes/
        │       └── vllm-runtime.yaml           # vLLM ServingRuntime (auto-deployed)
        └── tasks/
            ├── install.yaml                    # Namespace + runtimes + LLMInferenceService CRDs
            ├── deploy_models.yaml              # Deploys default-models.yaml (initial install only)
            ├── teardown.yaml
            └── validate.yaml
```

---

## Key Design Patterns

These patterns are what let Intel® AI for Enterprise Solutions stay modular — new components, repos, and actions plug in without touching the dispatcher.

### Action Dispatch

Every role's `tasks/main.yaml` uses a single pattern:

```yaml
- name: "component | Dispatch action: {{ component_action }}"
  ansible.builtin.include_tasks: "{{ component_action }}.yaml"
```

The `component_action` variable is passed as an extra-var from the CLI to `playbooks/site.yaml`:
- `install` → tasks/install.yaml
- `teardown` → tasks/teardown.yaml
- `validate` → tasks/validate.yaml

This means adding a new action (e.g., `upgrade`) only requires adding CLI support and a new `tasks/upgrade.yaml` in each role.

### Cross-Repo Role Discovery

External repos are declared in per-layer manifests at `configs/repos/repos.<layer>.yaml` and cloned under `ext/<dest>/` on first run. For example, [`configs/repos/repos.erag.yaml`](../../configs/repos/repos.erag.yaml) declares both the inference and Intel® AI for Enterprise RAG repositories needed for that layer. Each entry may set `deployment_subdir` to locate the Ansible tree inside the repo:

```yaml
repos:
  - layer: "inference"
    url: "https://github.com/intel/enterprise-inference"
    dest: "enterprise.ai-inference"
    deployment_subdir: ""          # roles/, components.yaml, config.yaml at repo root
  - layer: "erag"
    url: "https://github.com/intel/enterprise-rag"
    dest: "enterprise.ai-erag"
    deployment_subdir: "deployment" # Ansible tree lives under deployment/
```

The installer composes `ANSIBLE_ROLES_PATH` at runtime by joining `roles:ext/<dest>/<deployment_subdir>/roles` for every repo, then appending the Kubespray roles. The committed `ansible.cfg` `roles_path` value is overridden by this env var, so the file stays stable across runs.

Solution configs are consumed from the env only. `init <layer>` seeds `env/<name>/config.<layer>.yaml` from the ext repo's `config.yaml` baseline (or from a flavour preset if `--flavour` is given); `install` reads that editable copy and never re-reads `ext/<repo>/config.yaml` at runtime. A layer is active for a run if `env/<name>/.solutions.yaml` records it (written by `init <layer>`).

### Variable Precedence

```
CLI: -e kserve_version=0.15.0                       ← highest (runtime override)
  ↓
env/<name>/global_config.yaml                       ← environment global overrides
  ↓
env/<name>/config.<solution>.yaml                   ← solution config (seeded by init)
  ↓
roles/<name>/defaults/main.yaml                     ← role defaults (lowest)
```

Config files are loaded and passed as extra-vars. This uses native Ansible variable precedence. No custom config merging. The ext repo `config.yaml` is a seed source for `init`, not a load-time layer for `install`.

### Component Resolution and Layers

Components are defined in `components.yaml` files (this repo + ext repos). Each component specifies:
- `layer` (infrastructure, platform, inference, application)
- Position in the YAML list (registry order — determines execution sequence within layer)
- `depends_on` (same-layer dependencies, auto-pulled when component is targeted)

**Four layers** (executed in order):

| Layer | Components | Installation |
|-------|-----------|--------------|
| infrastructure | kubernetes, storage | Auto-pulled by `install inference` or `install erag` |
| platform | cert_manager, istio, metallb, envoy_gateway, postgresql, keycloak*, object_store, seaweedfs, observability | Auto-pulled by `install inference` or `install erag` |
| inference | keycloak_config*, envoy_ai_gateway, kserve, litellm**, langfuse**, llm_services, nri_cpu_balloons*** | `install inference` (explicitly named, or auto-pulled by `install erag`) |
| erag | app_inference_models, app_pre_install, app_vector_databases, app_keycloak_config, app_apisix, app_chat_history, app_nats, app_fingerprint, app_hpa, app_pipeline, app_edp, app_mcp_gateway, app_ui, app_watcher, app_post_install | `install erag` (opt-in, requires `init erag` first) |

\* Keycloak + keycloak_config are auto-disabled when `auth_provider=litellm`
\*\* LiteLLM + Langfuse are auto-enabled when `auth_provider=litellm`
\*\*\* NRI CPU balloons enabled when `kubernetes_cpu_policy=nri-balloons` and `kubernetes_accelerator=cpu`

The `erag` layer must be explicitly targeted: `install erag --env <name>`. See [../../ext/enterprise.ai-erag/docs/README.md](../../ext/enterprise.ai-erag/docs/README.md) (exists after `init erag` clones the repo) for RAG-layer documentation.

Component resolution happens in `playbooks/includes/preflight.yaml`:
1. Load all `components.yaml` files (this repo + ext repos)
2. Resolve target (e.g., `kserve` → includes dependencies like `cert_manager`, `kubernetes`)
3. Sort by layer order, then component order
4. Pass resolved list to `site.yaml`

### Environment Workflow

Each environment is fully isolated under `env/<name>/`:

```bash
# Initialize new environment (seeds config + inventory template)
./es_auto_installer.sh init inference --env prod

# Edit config and inventory
vim env/prod/global_config.yaml
vim env/prod/inventory/hosts.yaml

# Deploy to that environment
./es_auto_installer.sh install inference --env prod
```

**Environment state**:
- `env/<name>/kubeconfig.yaml` — generated after cluster install
- `env/<name>/logs/` — per-run Ansible logs (timestamped)
- `env/<name>/.solutions.yaml` — records which layers this env was inited for (written by `init <layer>`)

**Multiple environments on one bastion**: Each environment uses its own inventory and kubeconfig, so you can manage dev/staging/prod from a single machine.

### Auto-Discovery of Models and Runtimes

The `llm_services` role uses `ansible.builtin.find` to auto-discover all YAML files in its `files/catalog/` and `files/runtimes/` directories. To deploy a new model or runtime, drop a YAML file — no code changes needed.


### Kubernetes Installation

The `kubernetes` role uses **Kubespray** (kubernetes-sigs/kubespray) to install upstream Kubernetes.

| Variable | Default | Purpose |
|----------|---------|---------|
| `kubernetes_version` | (set in `global_config.yaml`) | Kubernetes release |
| `kubespray_version` | (set in `global_config.yaml`) | Pinned Kubespray tag |
| `kubernetes_cni` | `calico` | CNI plugin |
| `kubernetes_container_manager` | `containerd` | Container runtime |
| `existing_kubernetes` | `""` | Set to absolute path of a BYO kubeconfig to skip Kubespray entirely |

**Smart detection**: If a working cluster is already available (`kubectl cluster-info` succeeds with the configured kubeconfig at `env/<name>/kubeconfig.yaml`), the install is skipped. Set `existing_kubernetes: /path/to/kubeconfig` in `global_config.yaml` to use an existing cluster.

**Kubespray override model** (layered):
1. Kubespray installer base defaults
2. `kubespray_user_overrides` (dict of individual overrides like `kube_version`, `kube_network_plugin`)
3. `kubespray_full_config_file` (path to complete group_vars file, takes precedence over user_overrides)

### Deployment Topologies

The kubernetes role supports two deployment modes, derived entirely from the inventory content in `env/<name>/inventory/hosts.yaml`:

#### 1. Localhost Installation (default)

When the inventory contains only `localhost` hosts, Kubespray installs on this machine. Edit `env/<name>/inventory/hosts.yaml`:

```yaml
# Option A — Localhost (uncommented by default in init)
all:
  hosts:
    localhost:
      ansible_connection: local
  children:
    kube_control_plane:
      hosts:
        localhost:
    kube_node:
      hosts:
        localhost:
    etcd:
      hosts:
        localhost:
```

```bash
./es_auto_installer.sh install inference --env local
```

#### 2. Remote Installation (SSH-based)

When the inventory contains remote hosts, Kubespray installs over SSH. Edit `env/<name>/inventory/hosts.yaml`:

```yaml
# Option C — Multi-node cluster
all:
  hosts:
    master1:
      ansible_host: 10.0.1.10
      ansible_user: ubuntu
    worker1:
      ansible_host: 10.0.1.20
      ansible_user: ubuntu
    worker2:
      ansible_host: 10.0.1.21
      ansible_user: ubuntu
  children:
    kube_control_plane:
      hosts:
        master1:
    kube_node:
      hosts:
        worker1:
        worker2:
    etcd:
      hosts:
        master1:
```

```bash
./es_auto_installer.sh install inference --env prod
```

#### 3. Existing Kubernetes Cluster (skip Kubespray)

To deploy components on an existing cluster without running Kubespray, set `existing_kubernetes: /path/to/kubeconfig` in `env/<name>/global_config.yaml`. The installer uses this kubeconfig directly — no need to copy it into the env dir.

### Running from a Bastion Node

When the Ansible controller (bastion) is **not** part of the cluster and reaches nodes via SSH, configure the inventory with bastion settings:

```yaml
# env/<name>/inventory/hosts.yaml
all:
  hosts:
    master1:
      ansible_host: 10.0.1.10
      ansible_user: ubuntu
    worker1:
      ansible_host: 10.0.1.20
      ansible_user: ubuntu
  vars:
    # Optional: if nodes are behind a jump host
    ansible_ssh_common_args: '-o ProxyJump=jumpuser@10.0.0.5:22'
  children:
    kube_control_plane:
      hosts:
        master1:
    kube_node:
      hosts:
        worker1:
    etcd:
      hosts:
        master1:
```

**Behind proxy** (in `env/<name>/global_config.yaml`):
```yaml
http_proxy: "http://proxy:8080"
https_proxy: "http://proxy:8080"
```

---

## File-by-File Reference

A file-level map of this repository and the external inference repo it composes with.

### enterprise-ai-solutions — Top Level

| File | Purpose |
|------|---------|
| `es_auto_installer.sh` | CLI wrapper. Actions: `configure`, `show`, `init <layer>`, `install`, `teardown`, `validate`, `status`. Flags: `--env <name>`, `--flavour <name>` (init only), `--upgrade` (init only), `--only` (skip dep auto-inclusion), `--skip <names>` (comma-separated layers/components to exclude), `--force` (skip confirmation), `--` (pass remaining args to ansible-playbook). |
| `ansible.cfg` | Ansible configuration. `roles_path` is overridden at runtime by composed `ANSIBLE_ROLES_PATH`. Uses default stdout callback with yaml result format, `timer` + `profile_tasks` callbacks. |
| `env/<name>/global_config.yaml` | Main config for environment: component versions, TLS settings, proxy, auth. Overrides ext repo and role defaults. Single place to change versions per environment. |
| `env/<name>/inventory/hosts.yaml` | Kubespray-compatible inventory. Defines control-plane, workers, etcd groups. Copied from `inventory/hosts.yaml` by `init`. |
| `inventory/hosts.yaml` | Inventory template with three options (localhost, single remote, multi-node). Used by `init` to seed new environments and as `ansible.cfg` default. |

### enterprise-ai-solutions — Playbooks

| File | Purpose |
|------|---------|
| `playbooks/site.yaml` | Universal dispatcher. Accepts `component_action` (install/teardown/validate) and `target` as extra-vars. Includes `preflight.yaml`, then runs resolved components with the specified action. |
| `playbooks/includes/preflight.yaml` | Component resolution logic. Loads ext repo configs, merges env-specific configs, resolves component dependencies, sorts by layer and registry order. Also verifies kubectl connectivity for non-teardown/validate actions. |
| `playbooks/status.yaml` | Cluster status reporting — shows installed namespaces, pods, helm releases, and endpoints. |

### enterprise-ai-solutions — Roles

#### kubernetes

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | Kubernetes version, Kubespray version/repo, networking CIDRs, CNI (`calico`), container runtime, kubeconfig path, proxy settings. **Version set per environment in `global_config.yaml`.** |
| `files/generate_reserved_cpus.sh` | CPU pinning script. Delegated to first control-plane node for accurate NUMA detection. |
| `tasks/main.yaml` | Dispatches to `{{ component_action }}.yaml` |
| `tasks/install.yaml` | Smart entry point: detects existing cluster (kubectl + kubeconfig), checks `existing_kubernetes` (BYO path), skips if cluster exists and version OK, otherwise runs precheck → install_kubespray (prep) → (installer runs cluster.yml) → post_install |
| `tasks/precheck.yaml` | System prerequisites: OS check (Linux), swap disable, kernel modules (`br_netfilter`, `overlay`), sysctl (`ip_forward`, `bridge-nf-call-iptables`), required packages (`curl`, `tar`, `git`, `python3-pip`, `python3-venv`, `sshpass`) |
| `tasks/install_kubespray.yaml` | **Prep phase only** (3-phase split): Clones Kubespray to `.kubespray/`, creates Python venv, reads inventory from `env/<name>/inventory/hosts.yaml`, applies `kubespray_user_overrides` or `kubespray_full_config_file`, then exits. Installer runs `cluster.yml` directly for live output. |
| `tasks/post_install.yaml` | **Post-kubespray phase**: Copies kubeconfig from first control-plane node to `env/<name>/kubeconfig.yaml`, waits for nodes ready, verifies helm, prints cluster summary. |
| `tasks/teardown.yaml` | Runs Kubespray `reset.yml` via 3-phase split (prep → installer runs reset → cleanup), removes kubeconfig |
| `tasks/validate.yaml` | Checks kubectl, kubeconfig exists, cluster reachable, node status, server version, summary report |

#### Other platform roles

All roles follow the same pattern: `defaults/main.yaml` for config, `tasks/main.yaml` for dispatch, action-specific task files.

| Role | Purpose |
|------|---------|
| `storage` | Storage engine. `defaults/main.yaml` holds the `storage_backend` selector + `storage_backends` registry; `resolve.yaml` (run in preflight) normalizes the selector into `_resolved_storage_backend`, maps it to a backend role, rejects `local-path` on multi-node clusters and validates the ONTAP values; the meta-role `include_role`s the resolved role. Adding a backend = registry row + a role. |
| `nfs_storage` | Storage backend: NFS server + NFS provisioner for ReadWriteMany volumes. Marks its SC cluster-default. Flat vars (`nfs_*`), idempotent. |
| `rook_ceph_storage` | Storage backend: Rook/Ceph cluster on raw block devices. CephFS provides ReadWriteMany; teardown device wiping is opt-in (`rook_ceph_teardown_wipe_devices`). |
| `netapp_trident_csi` | Storage backend: NetApp Trident operator + ONTAP `TridentBackendConfig` + StorageClass (`reclaimPolicy: Retain`, `volumeBindingMode: Immediate`). Splits its tasks by what they need (variables / nodes / array) so everything but the array probes runs offline. See [NetApp ONTAP](../deploy/netapp_ontap.md). |
| `metallb` | MetalLB for LoadBalancer services. IP ranges from `env/<name>/nodes.yaml` or `global_config.yaml`. |
| `cert_manager` | Helm install cert-manager (prerequisite for KServe, Istio, gateway TLS). |
| `istio` | Istio ambient mesh (1.27.x). All workload namespaces enrolled via `istio.io/dataplane-mode: ambient` label. Provides zero-trust mTLS without sidecars. |
| `envoy_gateway` | Helm install from OCI registry, waits for deployment rollout, TLS config, SecurityPolicy for auth. |
| `postgresql` | CNPG operator + PostgreSQL clusters for Keycloak, LiteLLM, Langfuse. |
| `keycloak` | Keycloak operator + instance. Auto-disabled when `auth_provider=litellm`. |
| `object_store` | Selects and configures the object store backend (seaweedfs). |
| `seaweedfs` | S3-compatible object storage for Loki, Tempo, Langfuse blob storage. |
| `observability` | Prometheus + Grafana + Loki + Tempo stack. |
| `nri_cpu_balloons` | NRI CPU-balloons plugin. Generates per-node `BalloonsPolicy` CRs based on NUMA topology. Enabled only when `kubernetes_cpu_policy=nri-balloons`. |

### enterprise-inference — External Repo

External repos live under `ext/<dest>/` and are cloned on first run based on per-layer manifests at `configs/repos/repos.<layer>.yaml`.

| File | Purpose |
|------|---------|
| `config.yaml` | Baseline for inference components. Seed source for `init` (copied to `env/<name>/config.inference.yaml`); not read at install time. |
| `components.yaml` | Component definitions for this repo (kserve, llm_services, etc.). |

### enterprise-inference — Roles

#### kserve

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | `kserve_version`, `kserve_namespace`, `kserve_deploy_mode`, `kserve_wait_timeout` |
| `tasks/install.yaml` | Installs KServe CRDs + controller, installs built-in serving runtimes, patches deploy mode to `RawDeployment` |
| `tasks/teardown.yaml` | Deletes KServe cluster resources and CRDs |
| `tasks/validate.yaml` | Asserts InferenceService CRD exists, controller has ready replicas ≥ 1 |

#### litellm

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | Version, namespace, sizing (single/multi-node), cache, gateway integration, TLS |
| `tasks/install.yaml` | Helm deploy LiteLLM proxy + Valkey cache. Creates `ReferenceGrant` so Envoy Gateway's `SecurityPolicy` can reference LiteLLM service for extAuth. Creates `no-auth SecurityPolicy` on LiteLLM's own HTTPRoute (LiteLLM validates keys internally). |
| `tasks/tls.yaml` | Provisions TLS certificate for the LiteLLM hostname |
| `tasks/teardown.yaml` | Helm uninstall + cleanup namespace |
| `tasks/validate.yaml` | Health check against LiteLLM `/health` endpoint |

#### langfuse

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | Version, namespace, PostgreSQL + S3 config |
| `tasks/install.yaml` | Deploys Langfuse for LLM observability (traces, cost tracking, prompt management) |
| `tasks/teardown.yaml` | Helm uninstall + cleanup |

#### keycloak_config

| File | Purpose |
|------|---------|
| `tasks/install.yaml` | Creates Keycloak realm `inference`, client `inference-client`, OIDC SecurityPolicy on gateway. Skipped when `auth_provider=litellm`. |
| `tasks/teardown.yaml` | Removes realm and client |

#### llm_services

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | `llm_services_namespace`, runtime + model settings |
| `tasks/install.yaml` | Creates namespace, applies `files/runtimes/*.yaml` (ServingRuntimes), installs LLMInferenceService CRDs + kserve-runtime-configs |
| `tasks/deploy_models.yaml` | Deploys models from `default-models.yaml` at initial install. Sets `MM_LITELLM_ENABLED=true` when `auth_provider=litellm` for auto-registration. |
| `tasks/teardown.yaml` | Deletes all InferenceServices, ServingRuntimes, and namespace |
| `tasks/validate.yaml` | Lists runtimes and deployed LLM services |
| `files/runtimes/vllm-runtime.yaml` | vLLM ServingRuntime: OpenAI-compatible API server |

---

## CLI Usage

All Intel® AI for Enterprise Solutions commands run from the repository root:

```bash
# One-time machine setup (installs Python 3.11+, yq)
./es_auto_installer.sh configure

# Create a new environment
./es_auto_installer.sh init inference

# Create environment with the Intel AI for Enterprise RAG solution
./es_auto_installer.sh init erag --env myenv

# Create environment with a specific Intel AI for Enterprise RAG pipeline flavour
./es_auto_installer.sh init erag --flavour docsum

# Show available layers and components
./es_auto_installer.sh show

# Install infrastructure + platform + inference (via dependency auto-pull)
./es_auto_installer.sh install inference --env local

# Install specific component (auto-pulls dependencies)
./es_auto_installer.sh install kserve --env local

# Install without dependencies
./es_auto_installer.sh install metallb --only --env local

# Install a layer
./es_auto_installer.sh install platform --env local

# Install opt-in Intel AI for Enterprise RAG layer
./es_auto_installer.sh install erag --env myenv

# Teardown everything (cluster included)
./es_auto_installer.sh teardown infrastructure --env local

# Teardown specific component
./es_auto_installer.sh teardown keycloak --env local

# Validate health
./es_auto_installer.sh validate inference --env local

# Override a variable at runtime (pass ansible args after --)
./es_auto_installer.sh install kserve --env local -- -e kserve_version=0.15.0

# Single component without dependency auto-inclusion
./es_auto_installer.sh install metallb --only --env local

# Pass additional ansible-playbook flags (verbose mode)
./es_auto_installer.sh install inference --env local -- -vvv

# Work with multiple environments
./es_auto_installer.sh install inference --env dev
./es_auto_installer.sh install inference --env staging
./es_auto_installer.sh install inference --env prod
```

---

## Version Updates

Every Intel® AI for Enterprise Solutions component version is pinned in config, not hardcoded — to update one:

1. Edit **one line** in `env/<name>/global_config.yaml`:
   ```yaml
   envoy_gateway_version: "1.4.0"    # was 1.3.0
   ```

2. Re-run:
   ```bash
   ./es_auto_installer.sh install envoy_gateway --only --env <name>
   ```

Alternatively, override at runtime without editing files:
```bash
./es_auto_installer.sh install envoy_gateway --only --env <name> -- -e envoy_gateway_version=1.4.0
```

---

## Extensibility

Intel® AI for Enterprise Solutions is designed to grow without changes to this repo — add roles, repos, models, runtimes, or actions using the patterns below.

### Adding a New Role

1. Create the role directory under either repo's `roles/`:
   ```
   roles/my_component/
   ├── meta/main.yaml
   ├── defaults/main.yaml
   └── tasks/
       ├── main.yaml          # include_tasks: "{{ component_action }}.yaml"
       ├── install.yaml
       ├── teardown.yaml
       └── validate.yaml
   ```

2. Add an entry to `components.yaml` (here or in an ext repo):
   ```yaml
   components:
     - name: my_component
       layer: platform
       order: 50
       enabled: true
       dependencies:
         - cert_manager
   ```

3. Add default variables to `roles/my_component/defaults/main.yaml` and override in `env/<name>/global_config.yaml` if needed.

### Adding a New Repo

See [Adding a Solution](adding_solutions.md) for the complete reference on creating a new solution layer with its own external repository. In brief:

1. Create `configs/repos/repos.<layer>.yaml` in ai-solutions with the repo entries.
2. In your repo, create `components.yaml`, `config.yaml`, and `roles/<component>/` directories.
3. Run `./es_auto_installer.sh init <layer>` to clone and seed configs.

The installer auto-composes `ANSIBLE_ROLES_PATH` at runtime to include all ext repo roles.

### Moving Roles Between Repos

Just `mv` the role directory. Ansible resolves roles by name from `roles_path` — no path coupling.

### Adding a New Model (No Code Changes)

Drop a YAML file in the appropriate role's `files/catalog/`:
```
ext/enterprise.ai-inference/roles/llm_services/files/catalog/my-new-llm.yaml
```

The install playbooks auto-discover all files in these directories.

### Adding a New Serving Runtime

Drop a YAML file:
```
ext/enterprise.ai-inference/roles/llm_services/files/runtimes/my-runtime.yaml
```

### Adding a New Action

1. Add CLI support in `es_auto_installer.sh` to accept the new action (e.g., `upgrade`)
2. Pass `component_action: upgrade` as an extra-var to `playbooks/site.yaml`
3. Add `tasks/upgrade.yaml` to each role that needs it

---

## Safety Features

Intel® AI for Enterprise Solutions builds these safeguards into every install and teardown run:

| Feature | How |
|---------|-----|
| **Fail-fast** | `any_errors_fatal: true` in ansible.cfg — stops entire run on any failure |
| **Idempotent** | Tasks use `failed_when: ... and 'already exists' not in stderr` patterns |
| **Dry-run** | Pass `-- --check` to forward Ansible's check mode |
| **Selective execution** | `--only` flag runs a single target without dependency auto-inclusion |
| **Validation** | Dedicated `validate` action with `ansible.builtin.assert` checks |
| **Timing** | `timer` + `profile_tasks` callbacks show per-task durations |

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Walk through installing the layers described here, step by step | [Deployment Guide](../deploy/install_platform.md) |
| See how a request actually flows through the gateway and inference layers | [Network Architecture](../deploy/networking.md) |
| Look up every setting that drives this architecture | [Configuration Reference](../customize/configuration.md) |
| Connect your own app or framework to the inference layer | [Integration Guide](../customize/integration.md) |
