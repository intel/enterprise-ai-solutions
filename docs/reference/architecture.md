# Architecture & Design

[← Docs Index](../README.md)

## Overview

Toolkit Manager is a modular, extensible deployment framework for a self-hosted Kubernetes cluster, an LLM inference serving stack, and opt-in applications on Intel® Xeon® servers — deployable on-premises, air-gapped, or in a private cloud. It is a shell script (`toolkit_manager.sh`) that drives Ansible playbooks.

This repository provides:
- **Infrastructure layer** — Kubernetes (via Kubespray), storage (local-path, NFS, Ceph, NetApp ONTAP via Trident, or your own CSI driver), and on rack-scale clusters the fabric roles
- **Platform layer** — Node Feature Discovery, NRI CPU balloons, cert-manager, Istio (ambient), MetalLB, metrics-server, the gateway (Envoy Gateway), PostgreSQL, Keycloak, object store (SeaweedFS), observability; opt-in Headlamp, KEDA and Velero
- **Multi-environment support** — Isolated configs under `env/<name>/`
- **Cross-repo orchestration** — `init <layer>` clones the solution repos a layer needs into `ext/`, and their roles plug in by name

Solution repositories contribute the layers above platform:
- **Inference layer** (inference-toolkit) — KServe, model serving (`llm_services` + `model-manager`), Envoy AI Gateway or agentgateway, LiteLLM (proxy + virtual keys), Langfuse (LLM observability), Keycloak realm setup, opt-in vLLM Semantic Router
- **Application layers** (opt-in) — Intel® Chatbot Toolkit (`erag`: RAG pipelines, UI, vector databases) and Intel® Agent Toolkit (`agent-toolkit`)

**Auth modes:** Keycloak is deployed by default (`keycloak_enabled: true`) and people sign in to every web UI through it. `auth_type` in `env/<name>/config.inference.yaml` selects the credential API clients send:
- `jwt` (default) — a Keycloak access token (client credentials) on `inference.<domain>`; LiteLLM + Langfuse optional (`litellm_enabled: true`)
- `litellm_vkey` — a LiteLLM virtual key on `litellm.<domain>`; LiteLLM checks the key and forwards to the model; Langfuse on unless `langfuse_enabled: false`

The gateway `SecurityPolicy` is deny-by-default in both modes: a route without its own `SecurityPolicy` needs a Keycloak token. Routes that check credentials themselves (UI logins, the LiteLLM API) attach their own policy.

---

## Layer Architecture

A **layer** is a group of components installed together; a **component** is one Ansible role. Toolkit Manager installs in ordered layers, each depending on the ones below it:

```
infrastructure  →  platform  →  inference  →  erag / agent-toolkit (opt-in)
```

| Layer | Components (in [configs/components.yaml](../../configs/components.yaml) unless noted) | Installed by |
|---|---|---|
| `infrastructure` | `kubernetes`, `storage`; `kata_containers` (`kata_containers_enabled`); rack-scale only (`rack_scale_enabled`): `fabric_inventory`, `fabric_netplan`, `fabric_sysctl`, `fabric_bgp`, `fabric_validate` | `install` of any layer |
| `platform` | `node_feature_discovery`, `nri_cpu_balloons`, `cert_manager`, `istio`, `metallb`, `metrics_server`, `gateway` (alias `envoy_gateway`), `postgresql`, `keycloak`, `object_store`, `seaweedfs`, `observability`; opt-in: `headlamp`, `keda`, `velero`; rack-scale: `nvidia_gpu_operator`, `gpu_observability` | `install` of `platform` or any layer above |
| `inference` | from inference-toolkit's `components.yaml`: `keycloak_config`, `envoy_ai_gateway` or `agentgateway` (`ai_gateway_provider`), `kserve`, `litellm`, `langfuse`, `llm_services`, `vllm_sr` (opt-in) | `install inference`, or a layer above it |
| `erag` | from enterprise-rag's `components.yaml` (the `app_*` roles) | `install erag`, after `init erag` |
| `agent-toolkit` | from enterprise-agent-toolkit's `components.yaml` | `install agent-toolkit`, after `init agent-toolkit` |

- **`install inference`** installs infrastructure + platform + inference (dependencies auto-pull)
- **`install erag`** and **`install agent-toolkit`** must be explicitly targeted, after `init` of that layer
- Each component's `enabled:` expression decides whether it runs; for example `nri_cpu_balloons` runs only with `kubernetes_cpu_policy: nri-balloons` and `kubernetes_accelerator: cpu`. `./toolkit_manager.sh show` prints the merged list for your checkout.

---

## Execution Flow

Take `./toolkit_manager.sh install inference --env local`.

```
./toolkit_manager.sh install inference --env local
│
├─ 1. Prepare the run (main in toolkit_manager.sh)
│   ├─ Check env/local/ exists (created by init)
│   ├─ Check every repo the target's layers need is cloned in ext/ (init clones; install never does)
│   ├─ Ensure tools: yq, kubectl, helm, the .venv/ Python venv, the Ansible collections
│   └─ Build the extra-vars (see Variable Precedence) and pick the kubeconfig:
│      existing_kubernetes if set, else env/local/kubeconfig.yaml
│
├─ 2. Run playbooks/site.yaml once, or three times when the cluster must be built
│   │   (no kubeconfig yet, not a BYO cluster):
│   ├─ pass 1: site.yaml with target=kubernetes → preflight + Kubespray prep
│   │          (inventory, env/local/kubespray_extra_auto.yml)
│   ├─ pass 2: Kubespray cluster.yml, run directly so its output streams live
│   └─ pass 3: site.yaml for the whole target, with the cluster up
│
playbooks/site.yaml (runs on localhost, against the cluster API)
│
├─ includes/preflight.yaml:
│   ├─ Detect the platform (Kubernetes or OpenShift)
│   ├─ Merge every ext/**/components.yaml into the registry
│   ├─ Check settings, compute node placement, resolve the storage backend
│
├─ includes/resolve_target.yaml:
│   ├─ Target → ordered component list (filter_plugins/resolve_components.py)
│   ├─ Refuse layers this env was not inited for
│   ├─ Run the upgrade checks (roles/upgrade_checks)
│   └─ Print the plan
│
├─ role: ownership → records what is already on the cluster
│
├─ For each layer: includes/run_layer.yaml
│   ├─ Write the deployment record (ConfigMap <layer>-deployment-manifest in default)
│   └─ For each component: includes/run_component.yaml
│       ├─ role: <component> → tasks/{{ component_action }}.yaml
│       └─ role: ownership   → labels what the component created
│
└─ The installer prints the completion banner
```

Teardown of `kubernetes` or `infrastructure` works the same way around Kubespray's `reset.yml`: first everything that runs on the cluster is removed, then the cluster is reset, then what the cluster ran on (the rack-scale fabric). It asks for confirmation first, because the reset destroys the cluster. See [Ownership labels](adding_solutions.md#ownership-labels) for what teardown removes.

---

## Repository Structure

Toolkit Manager spans this repository plus the solution repositories that plug into it.

### toolkit-manager

```
toolkit-manager/
├── toolkit_manager.sh                          # CLI: configure, init, show, install, teardown, validate, status, add-node, remove-node
├── es_auto_installer.sh                        # Former name of the CLI; forwards to toolkit_manager.sh
├── run_backup.sh                               # Back up, restore or list one layer with Velero
├── model-manager                               # Wrapper for the inference repo's model-manager, pointed at env/<name>/
├── VERSION                                     # Installer version (also infrastructure and platform's version)
├── ansible.cfg                                 # Ansible settings (roles_path overridden at run time)
├── requirements.txt                            # Python packages for the installer venv
├── collections/requirements.yml                # Ansible collections
├── configs/
│   ├── components.yaml                         # Layer and component registry of this repo
│   ├── defaults/                               # Seeds init copies into env/<name>/: global_config.yaml, nodes.yaml
│   ├── repos/repos.<layer>.yaml                # Which repos each solution layer needs, at which revision
│   ├── backup.yaml                             # Contract for a layer's backup_profile.yaml
│   └── openshift_prerequisites.yaml            # What the OpenShift prerequisite check looks for
├── inventory/
│   └── hosts.yaml                              # Inventory template, copied into env/<name>/inventory/ by init
├── playbooks/
│   ├── site.yaml                               # Entry point for install, teardown, validate, add-node, remove-node
│   ├── includes/                               # preflight, resolve_target, run_layer, run_component, OpenShift + Agent Toolkit preflights
│   ├── status.yaml                             # The status command
│   └── backup.yaml                             # Run by run_backup.sh
├── roles/                                      # One role per component, plus library roles (see File-by-File Reference)
├── filter_plugins/                             # Component resolution, upgrade checks, rack profile parser, node preflight, …
├── callback_plugins/ownership_record.py        # Records what each task created, for the ownership labels
├── charts/                                     # Local Helm charts (istio, velero)
├── scripts/                                    # Helper scripts (custom DNS, CSI snapshots)
├── src/                                        # Headlamp plugin source, unit and e2e tests
├── docs/                                       # These guides
├── ext/                                        # Solution repos, cloned by init (not committed)
├── .kubespray/                                 # Kubespray checkout + its venv, created on first cluster install
└── env/                                        # Per-environment configs + state (not committed)
    └── local/                                  # Example environment
        ├── global_config.yaml                  # Main settings
        ├── nodes.yaml                          # The machines; generates inventory/hosts.yaml when nodes are listed
        ├── inventory/
        │   └── hosts.yaml                      # Kubespray inventory
        ├── config.<layer>.yaml                 # One per solution layer, seeded by init (config.inference.yaml, …)
        ├── models.yaml                         # Model catalog, seeded by init inference
        ├── .solutions.yaml                     # Which layers this env was inited for
        ├── kubeconfig.yaml                     # Written when the installer builds the cluster
        └── logs/                               # One log per run, the generated CA certificate, credentials
```

### inference-toolkit (external repo)

Cloned to `ext/enterprise.ai-inference/` by `init inference`.

```
inference-toolkit/
├── components.yaml                             # The inference layer's components
├── config.yaml                                 # Seed for env/<name>/config.inference.yaml (not read at install)
├── version.yaml                                # The layer's version and upgrade path
├── model_manager/
│   ├── model-manager                           # Model CLI: deploy, undeploy, list, cpu-collisions-check
│   ├── models.yaml                             # Model catalog, seeded to env/<name>/models.yaml
│   └── core/, runtimes/, templates/, chat-templates/, scripts/
└── roles/                                      # agentgateway, envoy_ai_gateway, keycloak_config, kserve,
                                                # langfuse, litellm, llm_services, vllm_sr
```

The inference repo's own docs describe its roles and `model-manager` in detail.

---

## Key Design Patterns

These patterns are what let Toolkit Manager stay modular — new components, repos, and layers plug in without touching the dispatcher.

### Action Dispatch

Every component role's `tasks/main.yaml` is the same small dispatcher ([Roles](adding_solutions.md#4-roles)).
The `component_action` variable is passed as an extra-var from the CLI to `playbooks/site.yaml`:
- `install` → tasks/install.yaml
- `teardown` → tasks/teardown.yaml
- `validate` → tasks/validate.yaml

The dispatcher first loads `vars/<cluster_distribution>.yaml`, if the role has one; the task
files are the same on Kubernetes and OpenShift and take what differs from those values.

Adding a new action takes CLI support, an entry in `VALID_ACTIONS`, and a `tasks/<action>.yaml` in each role that supports it (see [Adding a New Action](#adding-a-new-action)).

### Cross-Repo Role Discovery

External repos are declared in per-layer manifests at `configs/repos/repos.<layer>.yaml`; `init <layer>` clones each one to `ext/<dest>/` (`install` never clones). For example, [`configs/repos/repos.erag.yaml`](../../configs/repos/repos.erag.yaml) declares both the inference and Intel® Chatbot Toolkit repositories needed for that layer. Each entry may set `deployment_subdir` to locate the Ansible tree inside the repo:

```yaml
repos:
  - layer: "inference"
    url: "https://github.com/intel/enterprise-inference"   # redirects to intel/inference-toolkit
    dest: "enterprise.ai-inference"
    deployment_subdir: ""          # roles/, components.yaml, config.yaml at repo root
  - layer: "erag"
    url: "https://github.com/intel/enterprise-rag"
    dest: "enterprise.ai-erag"
    deployment_subdir: "deployment" # Ansible tree lives under deployment/
```

The installer composes `ANSIBLE_ROLES_PATH` at runtime by adding `ext/<dest>/<deployment_subdir>/roles` for every cloned repo to this repo's `roles`, then the Kubespray roles. This env var overrides `roles_path` in `ansible.cfg`, so a role is found by its name wherever it lives.

Solution configs are consumed from the env only. `init <layer>` seeds `env/<name>/config.<layer>.yaml` from the ext repo's `config.yaml` baseline (or from a flavour preset if `--flavour` is given); `install` reads that editable copy and never re-reads `ext/<repo>/config.yaml` at runtime. A layer takes part in an env only if `env/<name>/.solutions.yaml` records it (written by `init <layer>`).

### Variable Precedence

All settings files are passed to Ansible as extra-vars (`-e @file`); a later one wins:

```
CLI: -- -e key=value                                ← highest (anything after --)
  ↓
kubernetes_kubeconfig                               ← the chosen kubeconfig
  ↓
env/<name>/nodes.yaml                               ← when it lists anything
  ↓
env/<name>/global_config.yaml                       ← environment-wide settings
  ↓
env/<name>/config.<layer>.yaml                      ← one per inited layer, dependencies first
  ↓
roles/<name>/defaults/main.yaml                     ← role defaults (lowest)
```

So a value in `global_config.yaml` overrides the same key in a layer config. This uses native Ansible variable precedence; there is no custom config merging: a nested dict in a later file replaces the earlier one whole, which is why role variables are flat (`my_api_replicas`). The ext repo `config.yaml` is a seed source for `init`, not a load-time layer for `install`.

### Component Resolution and Layers

Components are defined in `components.yaml` files (this repo + ext repos). Each component specifies:
- `layer` (`infrastructure`, `platform`, `inference`, or a solution layer such as `erag`)
- `depends_on` (the components it needs; execution order within a layer comes from these, and the order in the file only breaks ties)
- `enabled` (an expression; disabled components are skipped)

The layers and their components are listed under [Layer Architecture](#layer-architecture). The `erag` and `agent-toolkit` layers must be explicitly targeted, after `init` of that layer. See [../../ext/enterprise.ai-erag/docs/README.md](../../ext/enterprise.ai-erag/docs/README.md) (exists after `init erag` clones the repo) for RAG-layer documentation.

Component resolution happens in `playbooks/includes/resolve_target.yaml`, through `filter_plugins/resolve_components.py`:
1. A **layer** target pulls in the layers it depends on (`install`) or the layers that depend on it (`teardown`). `--only` turns that off.
2. Layers run in dependency order; within a layer, components run in `depends_on` order.
3. A **component** target runs that component and the components its `depends_on` names (for teardown: the components that depend on it).
4. `--skip` drops components or whole layers, then disabled components are dropped.
5. Teardown runs the list in reverse.

### Environment Workflow

Each environment is fully isolated under `env/<name>/`, with its own settings, inventory and kubeconfig:

```bash
# Initialize new environment (seeds config, nodes.yaml, inventory template; clones the inference repo)
./toolkit_manager.sh init inference --env prod

# Edit config and the machine list
vim env/prod/global_config.yaml
vim env/prod/nodes.yaml

# Deploy to that environment
./toolkit_manager.sh install inference --env prod
```

**Environment state**:
- `env/<name>/kubeconfig.yaml` — written when the installer builds the cluster
- `env/<name>/logs/` — one log per run (`<action>-<target>-<timestamp>.log`), the generated CA certificate, credentials
- `env/<name>/.solutions.yaml` — records which layers this env was inited for (written by `init <layer>`)

**Multiple environments on one bastion**: Each environment uses its own inventory and kubeconfig, so you can manage dev/staging/prod from a single machine. Every command takes `--env`; without it, `env/local/` is used.

### Auto-Discovery of Models and Runtimes

There is no file auto-discovery. Models are entries in the catalog `env/<name>/models.yaml` (seeded from the inference repo's `model_manager/models.yaml`), and `./model-manager deploy <name>` serves one. Adding a model is a catalog entry, not a code change; serving engines (`servers`) are part of each catalog entry. See [Deploy a Model](../deploy/deploy_models.md).

### Kubernetes Installation

The `kubernetes` role uses **Kubespray** (kubernetes-sigs/kubespray) to install upstream Kubernetes.

| Variable | Default | Purpose |
|----------|---------|---------|
| `kubernetes_version` | `1.35.4` | Kubernetes release |
| `kubespray_version` | `v2.31.0` | Pinned Kubespray tag, cloned to `.kubespray/` |
| `kubernetes_cni` | `calico` | CNI plugin |
| `kubernetes_container_manager` | `containerd` | Container runtime |
| `existing_kubernetes` | `""` | Set to absolute path of a BYO kubeconfig to skip Kubespray entirely |

**Which cluster the installer acts on**: `toolkit_manager.sh` doesn't use `KUBECONFIG` (or `~/.kube/config`) for selecting the cluster. It uses only:
- `existing_kubernetes: /path/to/kubeconfig` in `global_config.yaml`, for a cluster you bring (Kubespray is skipped)
- `env/<name>/kubeconfig.yaml`, for the managed cluster. If the cluster answers with it, provisioning is skipped. To reprovision, tear down `kubernetes` and install again.

**Kubespray override model** (layered):
1. Kubespray's own defaults
2. `env/<name>/kubespray_extra_auto.yml` — the settings the role derives (written by the role)
3. `env/<name>/kubespray_extra.yml` — your own Kubespray settings; passed last, so it wins

### Deployment Topologies

Where the cluster runs is set in `env/<name>/nodes.yaml` (or, for a cluster you bring, `global_config.yaml`). When `nodes.yaml` lists nodes, the installer writes `env/<name>/inventory/hosts.yaml` from it on every run. See [Multi-Node & BYO Cluster](../deploy/topologies.md) for full examples.

#### 1. Localhost Installation (default)

Leave `env/<name>/nodes.yaml` commented out. The inventory `init` copies installs Kubespray on this machine (`localhost`):

```bash
./toolkit_manager.sh install inference --env local
```

#### 2. Remote Installation (SSH-based)

List the machines in `env/<name>/nodes.yaml`; Kubespray installs over SSH. With only `nodes_control_plane`, that node also runs workloads:

```yaml
# env/<name>/nodes.yaml — multi-node cluster
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: cp1
nodes_workers:
  - ip: 10.0.1.20
    hostname: worker1
  - ip: 10.0.1.21
    hostname: worker2
```

Each node may override the SSH user and key with `ssh_user` / `ssh_key` (global defaults: `nodes_ssh_user`, `nodes_ssh_key`). Hostnames must be lower case.

```bash
./toolkit_manager.sh install inference --env prod
```

A hand-written inventory still works: leave `nodes_control_plane` empty and edit `env/<name>/inventory/hosts.yaml` in Kubespray format.

#### 3. Existing Kubernetes Cluster (skip Kubespray)

To deploy components on an existing cluster without running Kubespray, set `existing_kubernetes: /path/to/kubeconfig` in `env/<name>/global_config.yaml`. The installer uses this kubeconfig directly — no need to copy it into the env dir.

### Running from a Bastion Node

The Ansible controller (bastion) does **not** have to be part of the cluster; it reaches the nodes over SSH using the machines listed in `nodes.yaml`. If the nodes are behind a jump host, use a hand-written inventory and add it to the inventory vars:

```yaml
# env/<name>/inventory/hosts.yaml
all:
  vars:
    # Optional: if nodes are behind a jump host
    ansible_ssh_common_args: '-o ProxyJump=jumpuser@10.0.0.5:22'
```

**Behind proxy** (in `env/<name>/global_config.yaml`):
```yaml
http_proxy: "http://proxy:8080"
https_proxy: "http://proxy:8080"
no_proxy: "..."   # keep the defaults (cluster address ranges) and add your node subnet
```

---

## File-by-File Reference

A file-level map of this repository and the external inference repo it composes with.

### toolkit-manager — Top Level

| File | Purpose |
|------|---------|
| `toolkit_manager.sh` | The CLI. Actions: `configure`, `init <layer>`, `show`, `install`, `teardown`, `validate`, `status`, `add-node`, `remove-node`. Flags: `--env <name>`, `--flavour <name>` and `--upgrade` (init only), `--only` (skip dep auto-inclusion), `--skip <names>` (comma-separated layers/components to exclude), `--force` (skip confirmation; on `install`, run past a refusing upgrade check), `--dry-run` (add-node, remove-node), `--health` (status), `--node-lost` (remove-node), `--` (pass remaining args to ansible-playbook). See the [CLI Reference](../customize/cli.md). |
| `es_auto_installer.sh` | The CLI's former name. Runs `toolkit_manager.sh` with the same arguments and prints a notice. |
| `run_backup.sh` | Back up, restore or list one layer's backups with Velero: actions `backup`, `restore`, `list`, each with a `<layer>`; flags `--env <name>`, `--from <backup>` (restore), `--force`, `--`. Sources `toolkit_manager.sh` for its playbook arguments and runs `playbooks/backup.yaml`. See [Upgrade a Layer](../deploy/upgrade.md). |
| `model-manager` | Runs `ext/enterprise.ai-inference/model_manager/model-manager` against `env/<name>/` (`TKM_ENV` picks the env). |
| `ansible.cfg` | Ansible configuration: `any_errors_fatal`, default stdout callback with yaml result format, `timer` + `profile_tasks` callbacks. `roles_path` is overridden at runtime by the composed `ANSIBLE_ROLES_PATH`. |
| `configs/components.yaml` | This repo's layers and components. |
| `configs/defaults/*.yaml` | Seeds `init` copies into a new env: `global_config.yaml`, `nodes.yaml`. |
| `configs/repos/repos.<layer>.yaml` | The repos each solution layer needs ([Adding a Solution](adding_solutions.md#1-the-layer-manifest-in-toolkit-manager)). |
| `env/<name>/global_config.yaml` | Main config for the environment: domain, storage, TLS, proxy, auth, feature switches. Overrides role defaults and layer configs. |
| `env/<name>/nodes.yaml` | The machines of the cluster; when it lists nodes, the installer generates `inventory/hosts.yaml` from it. |
| `env/<name>/inventory/hosts.yaml` | Kubespray-compatible inventory. Defines control-plane, workers, etcd groups. Copied from `inventory/hosts.yaml` by `init`; regenerated from `nodes.yaml` when that lists nodes. |
| `inventory/hosts.yaml` | Inventory template (localhost by default). Used by `init` to seed new environments. |

### toolkit-manager — Playbooks

| File | Purpose |
|------|---------|
| `playbooks/site.yaml` | Universal entry point. Accepts `component_action` (install/teardown/validate/add-node/remove-node) and `target` as extra-vars. Runs preflight and resolution, then each layer. |
| `playbooks/includes/preflight.yaml` | Platform detection, registry merge (this repo + every `ext/**/components.yaml`), setting checks, node placement, storage resolution. |
| `playbooks/includes/resolve_target.yaml` | Component resolution: target → ordered component list, refuses layers the env was not inited for, runs the upgrade checks, prints the plan. |
| `playbooks/includes/run_layer.yaml` | One layer: writes its deployment record, then runs its components. |
| `playbooks/includes/run_component.yaml` | One component: its role's action, then the ownership labels. |
| `playbooks/includes/preflight_platform.yaml`, `preflight_openshift.yaml` | Platform facts; the OpenShift prerequisite check. |
| `playbooks/includes/agent_toolkit_preflight.yaml` | Node checks for the Agent Toolkit layer. |
| `playbooks/status.yaml` | Cluster status reporting — shows installed namespaces, pods, helm releases, and endpoints. |
| `playbooks/backup.yaml` | Run by `run_backup.sh`. |

### toolkit-manager — Roles

#### kubernetes

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | Kubernetes version (`1.35.4`), Kubespray version (`v2.31.0`)/repo, networking CIDRs, CNI (`calico`), container runtime, kubeconfig path, proxy settings. Override any of them in `global_config.yaml`. |
| `files/generate_reserved_cpus.sh` | CPU pinning script. Delegated to first control-plane node for accurate NUMA detection. |
| `files/node_preflight_probe.py`, `files/simulate_node_removal.py` | Node checks before Kubespray runs; the placement simulation `remove-node` runs. |
| `tasks/main.yaml` | The canonical dispatcher: runs `{{ component_action }}.yaml` |
| `tasks/install.yaml` | Entry point: skips provisioning for a BYO cluster (`existing_kubernetes`), checks for an existing cluster and its version, generates the inventory from `nodes.yaml`, runs the SSH and node preflights, the Kubespray prep (`install_kubespray.yaml`) → (installer runs `cluster.yml`) → `setup_kubeconfig.yaml` + `post_install.yaml` |
| `tasks/precheck.yaml` | System prerequisites: swap disable, kernel modules (`br_netfilter`, `overlay`), sysctl, required packages |
| `tasks/install_kubespray.yaml` | **Prep phase only** (3-phase split): clones Kubespray to `.kubespray/`, creates its Python venv, writes `env/<name>/kubespray_extra_auto.yml`, then exits. Installer runs `cluster.yml` directly for live output. |
| `tasks/post_install.yaml` | **Post-kubespray phase**: with `setup_kubeconfig.yaml`, writes `env/<name>/kubeconfig.yaml`, waits for nodes ready, verifies helm, prints cluster summary. |
| `tasks/add-node.yaml`, `tasks/remove-node.yaml` | The `add-node` / `remove-node` actions ([Node Scaling](../customize/node_scaling.md)). |
| `tasks/teardown.yaml` | Runs Kubespray `reset.yml` via 3-phase split (prep → installer runs reset → cleanup), removes kubeconfig. On a BYO cluster it only removes the workload-class labels. |
| `tasks/validate.yaml` | Checks kubectl, kubeconfig exists, cluster reachable, node status, server version, summary report |

#### Other platform roles

All roles follow the same pattern: `defaults/main.yaml` for config, `tasks/main.yaml` for dispatch, action-specific task files.

| Role | Purpose |
|------|---------|
| `storage` | Storage engine. `defaults/main.yaml` holds the `storage_backend` selector + `storage_backends` registry; `resolve.yaml` (run in preflight) maps the selector to a backend role, rejects `local-path` on multi-node clusters and validates the ONTAP values; the meta-role `include_role`s the resolved role: `local-path` (built in), `nfs_storage`, `rook_ceph_storage`, `netapp_trident_csi` or `custom_storage`. Adding a backend = registry row + a role. |
| `nfs_storage` | Storage backend: NFS server + NFS provisioner for ReadWriteMany volumes. Marks its SC cluster-default. Flat vars (`nfs_*`), idempotent. |
| `rook_ceph_storage` | Storage backend: Rook/Ceph cluster on raw block devices. CephFS provides ReadWriteMany; teardown device wiping is opt-in (`rook_ceph_teardown_wipe_devices`). |
| `netapp_trident_csi` | Storage backend: NetApp Trident operator + ONTAP `TridentBackendConfig` + StorageClass (`reclaimPolicy: Retain`, `volumeBindingMode: Immediate`). Splits its tasks by what they need (variables / nodes / array) so everything but the array probes runs offline. See [NetApp ONTAP](../deploy/netapp_ontap.md). |
| `custom_storage` | Storage backend for a CSI driver you installed yourself: installs nothing and uses your ReadWriteMany StorageClass. |
| `kata_containers` | Kata Containers runtime on the nodes that pass its check (opt-in). |
| `fabric_inventory`, `fabric_netplan`, `fabric_sysctl`, `fabric_bgp`, `fabric_validate` | Rack-scale fabric: inventory from `rack-profile.yaml`, NIC configuration, kernel settings, Calico BGP peers, health checks ([Rack-scale Deployment](../rack-scale-deployment.md)). |
| `node_feature_discovery` | Labels nodes with their hardware features (for example AMX), which model scheduling uses. |
| `metallb` | MetalLB for LoadBalancer services. IP range from `metallb_ip_range`, or derived from the node IPs. |
| `cert_manager` | Helm install cert-manager (issues TLS certificates for the gateway and KServe). |
| `istio` | Istio ambient mesh. Workload namespaces enrolled via the `istio.io/dataplane-mode: ambient` label ([labels](labels.md)). Provides mTLS between services without sidecars. |
| `metrics_server` | The resource-metrics API (`kubectl top`, CPU/memory autoscaling, Headlamp tiles). |
| `gateway`, `envoy_gateway` | The platform's front door (Gateway API): TLS, hostnames, the deny-by-default Keycloak JWT SecurityPolicy. `envoy_gateway` installs the Envoy Gateway controller it runs on. |
| `postgresql` | CNPG operator + the PostgreSQL cluster for Keycloak, LiteLLM, Langfuse. |
| `keycloak` | Keycloak operator + instance and the platform realm for sign-in. On by default (`keycloak_enabled`). |
| `headlamp` | Kubernetes web UI with Keycloak sign-in (opt-in, `headlamp_enabled`) ([Headlamp](../deploy/headlamp.md)). |
| `object_store` | The object store interface; selects the backend (`object_store_backend`: `seaweedfs` or `external`). |
| `seaweedfs` | S3-compatible object storage for Loki, Tempo, Langfuse blob storage. |
| `observability` | Prometheus + Grafana + Loki + Tempo + OpenTelemetry Collector stack ([Observability](../deploy/observability.md)). |
| `keda` | Event-driven autoscaling (opt-in, `keda_enabled`). |
| `velero` | Cluster backups (opt-in, `velero_enabled`; needs snapshot-capable storage). |
| `nvidia_gpu_operator`, `gpu_observability` | NVIDIA GPU nodes on rack-scale: the GPU Operator, DCGM metrics and dashboards. |
| `nri_cpu_balloons` | NRI CPU-balloons plugin. Generates per-node `BalloonsPolicy` CRs based on NUMA topology. Enabled only when `kubernetes_cpu_policy=nri-balloons` and `kubernetes_accelerator=cpu` ([NRI CPU Balloons](../customize/nri_cpu_balloons.md)). |
| `ownership` | Library role: labels what each component creates and deletes it on teardown. |
| `upgrade_checks` | Library role: deployment records, run classes and the checks before an upgrade. |
| `backup` | Library role used by `playbooks/backup.yaml`. |

### inference-toolkit — External Repo

External repos live under `ext/<dest>/` and are cloned by `init <layer>` based on per-layer manifests at `configs/repos/repos.<layer>.yaml`.

| File | Purpose |
|------|---------|
| `config.yaml` | Baseline for inference components. Seed source for `init` (copied to `env/<name>/config.inference.yaml`); not read at install time. |
| `components.yaml` | Component definitions for this repo (kserve, llm_services, etc.). |
| `version.yaml` | The layer's version and upgrade path, read by the upgrade checks. |
| `model_manager/models.yaml` | Default model catalog, seeded to `env/<name>/models.yaml`. |

### inference-toolkit — Roles

#### kserve

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | `kserve_version` (follows `envoy_gateway_upgrade`), `kserve_namespace`, `kserve_deploy_mode` (`Standard`), `kserve_wait_timeout` |
| `tasks/install.yaml` | Installs KServe CRDs + controller, its built-in serving runtimes, and the deploy mode |
| `tasks/teardown.yaml` | Deletes KServe cluster resources and CRDs |
| `tasks/validate.yaml` | Asserts the InferenceService CRD exists and the controller is ready |

#### litellm

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | Version, namespace, sizing, cache, gateway integration (`litellm_public_paths`), UI (`litellm-ui.<domain>`) |
| `tasks/install.yaml` | Helm deploy LiteLLM proxy + Valkey cache. Creates LiteLLM's HTTPRoute for the public API paths; with `auth_type=litellm_vkey` LiteLLM validates virtual keys itself, otherwise the gateway's Keycloak JWT policy applies. |
| `tasks/register_guardrail.yaml`, `tasks/hybrid_router_setup.yaml` | The content-safety guardrail; hybrid routing |
| `tasks/teardown.yaml` | Helm uninstall + cleanup namespace |
| `tasks/validate.yaml` | Health check against LiteLLM `/health/readiness` |

#### langfuse

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | Version, namespace, PostgreSQL + S3 config |
| `tasks/install.yaml` | Deploys Langfuse for LLM observability (traces, cost tracking, prompt management) |
| `tasks/litellm_wiring.yaml` | Sends LiteLLM's traces to Langfuse |
| `tasks/teardown.yaml` | Helm uninstall + cleanup |

#### keycloak_config

| File | Purpose |
|------|---------|
| `tasks/install.yaml` | Creates Keycloak realm `inference` and client `inference-client` (`keycloak_realm`, `keycloak_client_id`). |
| `tasks/teardown.yaml` | Removes realm and client |

#### llm_services

| File | Purpose |
|------|---------|
| `defaults/main.yaml` | `llm_services_namespace`, model deploy settings (`llm_services_deploy_models`, `llm_services_autodeploy_models`) |
| `tasks/install.yaml` | Creates the namespace, applies the serving runtimes, deploys the guard model when the guardrail is on, then the default models |
| `tasks/deploy_models.yaml` | Deploys catalog models marked `autodeploy: true` (or those named in `llm_services_autodeploy_models`) with `model-manager`. Registers them with LiteLLM when it is on. |
| `tasks/ui_deploy.yaml` | Model deploys from Headlamp (model-manager Jobs) |
| `tasks/teardown.yaml` | Deletes the deployed models, runtimes and namespace |
| `tasks/validate.yaml` | Lists runtimes and deployed LLM services |
| `files/runtimes/vllm-runtime.yaml` | vLLM ServingRuntime manifest (not used by the default model deploys) |

---

## CLI Usage

All Toolkit Manager commands run from the repository root. Every flag is in the [CLI Reference](../customize/cli.md).

```bash
# One-time machine setup (Python venv, yq, kubectl, helm; asks first)
./toolkit_manager.sh configure

# Create a new environment
./toolkit_manager.sh init inference

# Create environment with the Intel® Chatbot Toolkit solution
./toolkit_manager.sh init erag --env myenv

# Create environment with a specific Intel® Chatbot Toolkit pipeline flavour
./toolkit_manager.sh init erag --flavour docsum

# Show available layers and components
./toolkit_manager.sh show

# Install infrastructure + platform + inference (via dependency auto-pull)
./toolkit_manager.sh install inference --env local

# Install specific component (auto-pulls its depends_on)
./toolkit_manager.sh install kserve --env local

# Install without dependencies
./toolkit_manager.sh install metallb --only --env local

# Install a layer
./toolkit_manager.sh install platform --env local

# Install opt-in Intel® Chatbot Toolkit
./toolkit_manager.sh install erag --env myenv

# Teardown everything (cluster included; asks first)
./toolkit_manager.sh teardown infrastructure --env local

# Teardown specific component (and the components that depend on it; --only for just it)
./toolkit_manager.sh teardown keycloak --env local

# Validate health
./toolkit_manager.sh validate inference --env local

# Override a variable at runtime (pass ansible args after --)
./toolkit_manager.sh install cert_manager --only --env local -- -e certmanager_version=<version>

# Single component without dependency auto-inclusion
./toolkit_manager.sh install metallb --only --env local

# Pass additional ansible-playbook flags (verbose mode)
./toolkit_manager.sh install inference --env local -- -vvv

# Work with multiple environments
./toolkit_manager.sh install inference --env dev
./toolkit_manager.sh install inference --env staging
./toolkit_manager.sh install inference --env prod
```

---

## Version Updates

Component versions are pinned in role defaults (`roles/<role>/defaults/main.yaml`), not hardcoded in tasks — to update one:

1. Add **one line** to `env/<name>/global_config.yaml`, for example for cert-manager:
   ```yaml
   certmanager_version: "<new version>"    # role default: roles/cert_manager/defaults/main.yaml
   ```

2. Re-run:
   ```bash
   ./toolkit_manager.sh install cert_manager --only --env <name>
   ```

Alternatively, override at runtime without editing files:
```bash
./toolkit_manager.sh install cert_manager --only --env <name> -- -e certmanager_version=<new version>
```

Some components have a release switch instead of a free version: `envoy_gateway_version` is derived from `envoy_gateway_upgrade` (`true` selects the newer release, and the KServe release that matches it). To upgrade a whole layer to a newer release, follow [Upgrade a Layer](../deploy/upgrade.md).

---

## Extensibility

Toolkit Manager is designed to grow without changes to this repo's core — add roles, repos, models, or actions using the patterns below.

### Adding a New Role

1. Create the role directory under either repo's `roles/`:
   ```
   roles/my_component/
   ├── defaults/main.yaml     # every variable it reads, with a working default
   └── tasks/
       ├── main.yaml          # the standard dispatcher
       ├── install.yaml
       ├── teardown.yaml
       └── validate.yaml
   ```

2. Add an entry to `components.yaml` (here or in an ext repo):
   ```yaml
   components:
     - name: my_component
       layer: platform
       enabled: "{{ my_component_enabled | default(true) | bool }}"
       depends_on:
         - cert_manager
   ```

3. Add default variables to `roles/my_component/defaults/main.yaml` and override in `env/<name>/global_config.yaml` if needed. Check it with `./toolkit_manager.sh show`. The full contract (fields, file rules, ownership labels) is in [Adding a Solution](adding_solutions.md).

### Adding a New Repo

See [Adding a Solution](adding_solutions.md) for the complete reference on creating a new solution layer with its own external repository. In brief:

1. Create `configs/repos/repos.<layer>.yaml` in toolkit-manager with the repo entries.
2. In your repo, create `components.yaml`, `config.yaml`, and `roles/<component>/` directories.
3. Run `./toolkit_manager.sh init <layer>` to clone and seed configs.

The installer auto-composes `ANSIBLE_ROLES_PATH` at runtime to include all ext repo roles.

### Moving Roles Between Repos

Just `mv` the role directory. Ansible resolves roles by name from `ANSIBLE_ROLES_PATH` — no path coupling.

### Adding a New Model (No Code Changes)

Add an entry to the model catalog `env/<name>/models.yaml` (or to `model_manager/models.yaml` in the inference repo, so every new env gets it), then deploy it:
```
./model-manager deploy my-new-llm
```

See [Deploy a Model](../deploy/deploy_models.md) for the catalog fields.

### Adding a New Serving Runtime

Serving engines are chosen per model in the catalog (`servers`, `default_server`; `vllm`, `openvino`, `vllm-cuda`), and their templates live in the inference repo's `model_manager/runtimes/`. A new engine is a change in the inference repo, not a file dropped into a role.

### Adding a New Action

1. Accept the action in `parse_args` and `main` in `toolkit_manager.sh`
2. Add it to `VALID_ACTIONS` in `filter_plugins/resolve_components.py`; the installer passes `component_action: <action>` to `playbooks/site.yaml`
3. Allow it in the dispatcher (`component_action` check) of each role that supports it, and add `tasks/<action>.yaml` to those roles

---

## Safety Features

Toolkit Manager builds these safeguards into every install and teardown run:

| Feature | How |
|---------|-----|
| **Fail-fast** | `any_errors_fatal = true` in ansible.cfg — stops entire run on any failure |
| **Idempotent** | A second `install` with no change makes no change ([role contract](adding_solutions.md#4-roles)) |
| **Dry-run** | Pass `-- --check` to forward Ansible's check mode |
| **Confirmation** | Teardown asks first and names what it removes; `--force` skips the prompt |
| **Teardown removes only what install made** | Ownership labels ([details](adding_solutions.md#ownership-labels)) |
| **Upgrade checks** | Run before an upgrade changes anything ([Upgrade a Layer](../deploy/upgrade.md)) |
| **Selective execution** | `--only` flag runs a single target without dependency auto-inclusion; `--skip` leaves components out |
| **Validation** | Dedicated `validate` action runs each role's read-only checks |
| **Timing** | `timer` + `profile_tasks` callbacks show per-task durations |

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Walk through installing the layers described here, step by step | [Deployment Guide](../deploy/install_platform.md) |
| See how a request actually flows through the gateway and inference layers | [Network Architecture](../deploy/networking.md) |
| Look up every setting that drives this architecture | [Configuration Reference](../customize/configuration.md) |
| Connect your own app or framework to the inference layer | [Integration Guide](../customize/integration.md) |
| Grow or shrink a cluster after it's already provisioned | [Node Scaling](../customize/node_scaling.md) |
