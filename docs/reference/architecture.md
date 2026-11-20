# Architecture & Design

[← Docs Index](../README.md)

How Toolkit Manager is put together: the layers it installs, what happens when you run a
command, where each file lives, and the patterns that let new components plug in. For
installing step by step, see the [Deployment Guide](../deploy/install_platform.md).

## Overview

Toolkit Manager deploys a self-hosted Kubernetes cluster, an LLM inference stack and opt-in
applications on Intel® Xeon® servers, on-premises, air-gapped or in a private cloud. It is a
shell script (`toolkit_manager.sh`) that drives Ansible playbooks.

- **This repository** holds the installer, the `infrastructure` and `platform` layers, and
  the registry that declares the `inference` layer.
- **Solution repositories** add layers on top. `init <layer>` clones them into `ext/`:
  - inference-toolkit: the `inference` layer (KServe, model serving, LiteLLM, Langfuse);
  - enterprise-rag: the `erag` layer (Intel® Chatbot Toolkit);
  - enterprise-agent-toolkit: the `agent-toolkit` layer (Intel® Agent Toolkit).
- **Environments** keep each deployment's settings apart, under `env/<name>/`.

**Sign-in.** Keycloak is deployed by default (`keycloak_enabled: true`) and people sign in to
every web UI through it. `auth_type`, in `env/<name>/config.inference.yaml`, selects what API
clients send:

- `jwt` (default): a Keycloak access token on `inference.<domain>`. LiteLLM and Langfuse are
  optional (`litellm_enabled: true`).
- `litellm_vkey`: a LiteLLM virtual key on `litellm.<domain>`. LiteLLM checks the key and
  forwards to the model; Langfuse is on unless `langfuse_enabled: false`.

The gateway denies by default in both modes: a route without its own `SecurityPolicy` needs a
Keycloak token. Routes that check credentials themselves (UI logins, the LiteLLM API) attach
their own policy.

---

## Layers and components

A **layer** is a group of components installed together; a **component** is one Ansible role.
Each layer depends on the ones below it, and `install <layer>` installs those first.

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

Each component's `enabled:` expression in `components.yaml` decides whether it runs; for
example `nri_cpu_balloons` runs only with `kubernetes_cpu_policy: nri-balloons` and
`kubernetes_accelerator: cpu`. `./toolkit_manager.sh show` prints the merged list for your
checkout.

---

## What happens when you run `install`

Take `./toolkit_manager.sh install inference --env local`.

**1. The installer script prepares the run** (`main` in `toolkit_manager.sh`):

1. Checks that `env/local/` exists (created by `init`).
2. Checks that every repo the target's layers need is cloned in `ext/` (`init` clones them;
   `install` never does).
3. Makes sure the tools are there: `yq`, `kubectl`, `helm`, the Python venv in `.venv/` and
   the Ansible collections.
4. Builds the Ansible extra-vars (see [Variable precedence](#variable-precedence)) and picks
   the kubeconfig: `existing_kubernetes` if set, else `env/local/kubeconfig.yaml`.

**2. It runs `playbooks/site.yaml`, once or three times.** A plain run is one pass. When the
cluster still has to be built (no kubeconfig yet, not a BYO cluster) it runs three passes:

1. `site.yaml` with `target=kubernetes`: preflight and the Kubespray preparation
   (inventory, `env/local/kubespray_extra_auto.yml`).
2. Kubespray's `cluster.yml`, run directly so its output streams live.
3. `site.yaml` for the whole target, with the cluster now up.

Teardown of `kubernetes` or `infrastructure` works the same way around Kubespray's
`reset.yml`: first everything that runs on the cluster is removed, then the cluster is
reset, then what the cluster ran on (the rack-scale fabric). It asks for confirmation
first, because the reset destroys the cluster.

**3. `playbooks/site.yaml` runs the components**, on the installer host (`localhost`), against
the cluster's API:

1. `includes/preflight.yaml`: detects the platform (Kubernetes or OpenShift), merges every
   `ext/**/components.yaml` into the registry, checks settings, computes node placement and
   resolves the storage backend.
2. `includes/resolve_target.yaml`: turns the target into the ordered list of components to
   run (`filter_plugins/resolve_components.py`), refuses layers this env was not inited for,
   runs the upgrade checks (`roles/upgrade_checks`) and prints the plan.
3. The `ownership` role records what is already on the cluster.
4. For each layer, `includes/run_layer.yaml` writes the layer's deployment record (ConfigMap
   `<layer>-deployment-manifest` in `default`), then `includes/run_component.yaml` runs each
   component: its role's `tasks/<action>.yaml`, and afterwards the `ownership` role labels
   what it created (see [Ownership labels](adding_solutions.md#ownership-labels)).
5. The installer prints the completion banner.

---

## Repository structure

### This repository (Toolkit Manager)

```
toolkit-manager/
├── toolkit_manager.sh           # the CLI: configure, init, show, install, teardown, validate, status, add-node, remove-node
├── es_auto_installer.sh         # former name of the CLI; forwards to toolkit_manager.sh
├── run_backup.sh                # back up, restore or list one layer with Velero
├── model-manager                # wrapper for the inference repo's model-manager, pointed at env/<name>/
├── VERSION                      # installer version (also the version of infrastructure and platform)
├── ansible.cfg                  # Ansible settings
├── requirements.txt             # Python packages for the installer venv
├── collections/requirements.yml # Ansible collections
├── configs/
│   ├── components.yaml          # layer and component registry of this repo
│   ├── defaults/                # seeds copied into env/<name>/ by init: global_config.yaml, nodes.yaml
│   ├── repos/repos.<layer>.yaml # which repos each solution layer needs, at which revision
│   ├── backup.yaml              # contract for a layer's backup_profile.yaml
│   └── openshift_prerequisites.yaml  # what the OpenShift prerequisite check looks for
├── inventory/hosts.yaml         # inventory template, copied into env/<name>/inventory/ by init
├── playbooks/
│   ├── site.yaml                # entry point for install, teardown, validate, add-node, remove-node
│   ├── status.yaml              # the status command
│   ├── backup.yaml              # run by run_backup.sh
│   └── includes/                # preflight, resolve_target, run_layer, run_component, OpenShift and Agent Toolkit preflights
├── roles/                       # one role per component, plus the library roles (see below)
├── filter_plugins/              # Python filters: component resolution, upgrade checks, rack profile parser, node preflight, …
├── callback_plugins/ownership_record.py  # records what each task created, for the ownership labels
├── charts/                      # local Helm charts (istio, velero)
├── scripts/                     # helper scripts (custom DNS, CSI snapshots)
├── src/                         # Headlamp plugin source, unit and e2e tests
├── docs/                        # these guides
├── ext/                         # solution repos, cloned by init (not committed)
├── .kubespray/                  # Kubespray checkout and its venv, created on first cluster install
└── env/<name>/                  # one directory per environment (not committed)
    ├── global_config.yaml       # main settings
    ├── nodes.yaml               # the machines; generates inventory/hosts.yaml when nodes are listed
    ├── inventory/hosts.yaml     # Kubespray inventory
    ├── config.<layer>.yaml      # one per solution layer, seeded by init
    ├── models.yaml              # model catalog, seeded by init inference
    ├── .solutions.yaml          # which layers this env was inited for
    ├── kubeconfig.yaml          # written when the installer builds the cluster
    └── logs/                    # one log per run, the generated CA certificate and credentials
```

### inference-toolkit (cloned to `ext/enterprise.ai-inference/`)

```
inference-toolkit/
├── components.yaml              # the inference layer's components
├── config.yaml                  # seed for env/<name>/config.inference.yaml
├── version.yaml                 # the layer's version and upgrade path
├── model_manager/
│   ├── model-manager            # model CLI: deploy, undeploy, list, …
│   ├── models.yaml              # model catalog, seeded to env/<name>/models.yaml
│   ├── core/, runtimes/, templates/, chat-templates/, scripts/
└── roles/                       # agentgateway, envoy_ai_gateway, keycloak_config, kserve, langfuse, litellm, llm_services, vllm_sr
```

The inference repo's own docs describe its roles and `model-manager` in detail.

---

## Key design patterns

These patterns let new components, repos and layers plug in without changing the core.

### One dispatcher per role

Every component role's `tasks/main.yaml` is the same small dispatcher
([Roles](adding_solutions.md#4-roles)). It checks `component_action` (`install`, `teardown` or
`validate`), loads `vars/<cluster_distribution>.yaml` if the role has one, and includes
`tasks/<component_action>.yaml`. The task files are the same on Kubernetes and OpenShift;
what differs is a value in those `vars/` files.

### Roles found by name across repos

`configs/repos/repos.<layer>.yaml` lists the repos a layer needs; `init` clones each to
`ext/<dest>/`. For every cloned repo the installer adds `ext/<dest>/<deployment_subdir>/roles`
to `ANSIBLE_ROLES_PATH` (overriding `roles_path` in `ansible.cfg`), so a role is found by its
name wherever it lives. Moving a role between repos is a `mv`.

Solution settings come from the env only: `init <layer>` copies the repo's `config.yaml` (or
the chosen `--flavour` preset) to `env/<name>/config.<layer>.yaml`, and `install` reads that
copy. A layer takes part in an env only when `env/<name>/.solutions.yaml` lists it.

### Variable precedence

All settings files are passed to Ansible as extra-vars (`-e @file`); a later one wins. The
installer passes them in this order:

```
roles/<role>/defaults/main.yaml          ← lowest: the role's defaults
env/<name>/config.<layer>.yaml           ← one per inited layer, dependencies first
env/<name>/global_config.yaml            ← environment-wide settings
env/<name>/nodes.yaml                    ← when it lists anything
kubernetes_kubeconfig                    ← the chosen kubeconfig
-- -e key=value                          ← highest: anything after -- on the command line
```

So a value in `global_config.yaml` overrides the same key in a layer config. There is no custom
merging: a nested dict in a later file replaces the earlier one whole, which is why role
variables are flat (`my_api_replicas`).

### Component resolution

`filter_plugins/resolve_components.py` turns a target into the list of components to run:

1. A **layer** target pulls in the layers it depends on (`install`) or the layers that depend
   on it (`teardown`). `--only` turns that off.
2. Layers run in dependency order; within a layer, components run in `depends_on` order, and
   the order in the file breaks ties.
3. A **component** target runs that component and the components its `depends_on` names
   (for teardown: the components that depend on it).
4. `--skip` drops components or whole layers, then disabled components are dropped.
5. Teardown runs the list in reverse.

### Environments

Each environment is a directory under `env/<name>/` with its own settings, inventory and
kubeconfig, so one machine can manage several clusters:

```bash
./toolkit_manager.sh init inference --env prod   # 1. create env/prod/ and clone the inference repo
vi env/prod/global_config.yaml                    # 2. set the domain, storage, …
vi env/prod/nodes.yaml                            # 3. list the machines (or leave empty for this one)
./toolkit_manager.sh install inference --env prod # 4. deploy
```

Every command takes `--env`; without it, `env/local/` is used.

### Models

Models are entries in the catalog `env/<name>/models.yaml` (seeded from the inference repo's
`model_manager/models.yaml`). `./model-manager deploy <name>` serves one. Adding a model is
a catalog entry, not a code change; see [Deploy a Model](../deploy/deploy_models.md).

### Kubernetes installation

The `kubernetes` role installs upstream Kubernetes with [Kubespray](https://github.com/kubernetes-sigs/kubespray).

| Variable | Default | Purpose |
|---|---|---|
| `kubernetes_version` | `1.35.4` | Kubernetes release |
| `kubespray_version` | `v2.31.0` | Kubespray release, cloned to `.kubespray/` |
| `kubernetes_cni` | `calico` | Network plugin |
| `kubernetes_container_manager` | `containerd` | Container runtime |
| `existing_kubernetes` | `""` | Path of a kubeconfig for a cluster you bring; Kubespray is then never run |

The role writes `env/<name>/kubespray_extra_auto.yml` with the settings it derives. To change
any other Kubespray setting, put it in `env/<name>/kubespray_extra.yml`; it is passed last, so
it wins.

**Which cluster the installer talks to.** Not `KUBECONFIG` or `~/.kube/config`. Only:

- `existing_kubernetes: /path/to/kubeconfig` in `global_config.yaml`, for a cluster you bring;
- otherwise `env/<name>/kubeconfig.yaml`, written when the installer built the cluster. If
  the cluster answers with it, provisioning is skipped. To rebuild, tear down `kubernetes`
  and install again.

### Where the cluster runs

| Setup | What to do |
|---|---|
| This machine only (default) | Leave `env/<name>/nodes.yaml` commented out. The inventory installs on `localhost`. |
| Remote machines over SSH | List them in `env/<name>/nodes.yaml` (`nodes_control_plane`, optionally `nodes_workers`, with `ip` and `hostname`). The installer writes `inventory/hosts.yaml` from it. |
| A hand-written inventory | Leave `nodes_control_plane` empty and edit `env/<name>/inventory/hosts.yaml` in Kubespray format. |
| A cluster you already have | Set `existing_kubernetes` in `global_config.yaml`. |

See [Multi-Node & BYO Cluster](../deploy/topologies.md) for full examples.

**From a bastion host.** The installer host does not have to be in the cluster; it reaches
the nodes over SSH. If they are behind a jump host, add it to the inventory vars:

```yaml
# env/<name>/inventory/hosts.yaml
all:
  vars:
    ansible_ssh_common_args: '-o ProxyJump=jumpuser@10.0.0.5:22'
```

**Behind a proxy**, set `http_proxy`, `https_proxy` and `no_proxy` in
`env/<name>/global_config.yaml`.

---

## File reference

### Top level

| File | Purpose |
|---|---|
| `toolkit_manager.sh` | The CLI. Actions: `configure`, `init <layer>`, `show`, `install`, `teardown`, `validate`, `status`, `add-node`, `remove-node`. Flags: `--env`, `--flavour` and `--upgrade` (init), `--only`, `--skip`, `--force`, `--dry-run` (add-node, remove-node), `--health` (status), `--node-lost` (remove-node), and `--` to pass the rest to `ansible-playbook`. See the [CLI Reference](../customize/cli.md). |
| `es_auto_installer.sh` | The CLI's former name. Runs `toolkit_manager.sh` with the same arguments and prints a notice. |
| `run_backup.sh` | Back up, restore or list one layer with Velero: `backup`, `restore`, `list <layer>`; flags `--env`, `--from <backup>` (restore), `--force`, `--`. Uses the installer's own functions and runs `playbooks/backup.yaml`. See [Upgrade a Layer](../deploy/upgrade.md). |
| `model-manager` | Runs `ext/enterprise.ai-inference/model_manager/model-manager` against `env/<name>/` (`TKM_ENV`, default `local`). |
| `ansible.cfg` | Ansible settings: `any_errors_fatal`, the `timer` and `profile_tasks` callbacks, plugin paths. `roles_path` is overridden at run time. |
| `configs/components.yaml` | This repo's layers and components. |
| `configs/defaults/*.yaml` | Seeds `init` copies into a new env: `global_config.yaml`, `nodes.yaml`. |
| `configs/repos/repos.<layer>.yaml` | The repos each solution layer needs ([Adding a Solution](adding_solutions.md#1-the-layer-manifest-in-toolkit-manager)). |
| `inventory/hosts.yaml` | Inventory template copied into each env by `init`. |

### Playbooks

| File | Purpose |
|---|---|
| `playbooks/site.yaml` | The entry point. Takes `component_action` and `target`, runs preflight and resolution, then each layer. |
| `playbooks/includes/preflight.yaml` | Platform detection, registry merge, setting checks, placement, storage resolution. |
| `playbooks/includes/resolve_target.yaml` | Target to component list, provisioning check, upgrade checks, the printed plan. |
| `playbooks/includes/run_layer.yaml` | One layer: its deployment record and its components. |
| `playbooks/includes/run_component.yaml` | One component: its role, and the ownership labels. |
| `playbooks/includes/preflight_platform.yaml`, `preflight_openshift.yaml` | Platform facts; the OpenShift prerequisite check. |
| `playbooks/includes/agent_toolkit_preflight.yaml` | Node checks for the Agent Toolkit layer. |
| `playbooks/status.yaml` | The `status` command: namespaces, pods, Helm releases, endpoints. |
| `playbooks/backup.yaml` | Run by `run_backup.sh`. |

### Roles in this repo

| Role | Purpose |
|---|---|
| `kubernetes` | Builds the cluster with Kubespray (or uses yours), writes the kubeconfig, adds and removes nodes. |
| `storage` | Picks the storage backend from `storage_backend` and runs its role: `local-path` (built in), `nfs_storage`, `rook_ceph_storage`, `netapp_trident_csi` ([NetApp ONTAP](../deploy/netapp_ontap.md)) or `custom_storage` (your own CSI driver). |
| `kata_containers` | Kata Containers runtime on the nodes that pass its check (opt-in). |
| `fabric_inventory`, `fabric_netplan`, `fabric_sysctl`, `fabric_bgp`, `fabric_validate` | Rack-scale fabric: inventory from `rack-profile.yaml`, NIC configuration, kernel settings, Calico BGP peers, health checks ([Rack-scale Deployment](../rack-scale-deployment.md)). |
| `node_feature_discovery` | Labels nodes with their hardware features (for example AMX), which model scheduling uses. |
| `nri_cpu_balloons` | NUMA-aware CPU pinning for model pods ([NRI CPU Balloons](../customize/nri_cpu_balloons.md)). |
| `cert_manager` | Issues TLS certificates. |
| `istio` | Istio ambient mesh: encrypts traffic between services without sidecars ([labels](labels.md)). |
| `metallb` | Gives `LoadBalancer` Services an IP on bare metal. |
| `metrics_server` | The resource-metrics API (`kubectl top`, CPU/memory autoscaling, Headlamp tiles). |
| `gateway` | The platform's front door (Gateway API): TLS, hostnames, the deny-by-default sign-in policy. `envoy_gateway` is its implementation. |
| `postgresql` | CloudNativePG operator and the database cluster for Keycloak, LiteLLM and Langfuse. |
| `keycloak` | Keycloak operator, instance and the platform realm for sign-in. |
| `headlamp` | Kubernetes web UI with Keycloak sign-in (opt-in, `headlamp_enabled`) ([Headlamp](../deploy/headlamp.md)). |
| `object_store`, `seaweedfs` | S3 object storage for logs, traces and Langfuse: `object_store` is the interface, `seaweedfs` the default backend. |
| `observability` | Prometheus, Grafana, Loki, Tempo and the OpenTelemetry Collector ([Observability](../deploy/observability.md)). |
| `keda` | Event-driven autoscaling (opt-in, `keda_enabled`). |
| `velero` | Cluster backups (opt-in, `velero_enabled`; needs snapshot-capable storage). |
| `nvidia_gpu_operator`, `gpu_observability` | NVIDIA GPU nodes on rack-scale: the GPU Operator, DCGM metrics and dashboards. |
| `ownership` | Library role: labels what each component creates and deletes it on teardown. |
| `upgrade_checks` | Library role: deployment records, run classes and the checks before an upgrade. |
| `backup` | Library role used by `playbooks/backup.yaml`. |

---

## CLI usage

The common commands, run from the repository root. Every flag is in the
[CLI Reference](../customize/cli.md).

```bash
./toolkit_manager.sh configure                                  # once per machine: Python venv, yq, kubectl, helm
./toolkit_manager.sh init inference --env local                 # create env/local/ for the inference layer
./toolkit_manager.sh init erag --flavour docsum --env local      # Intel® Chatbot Toolkit with a pipeline preset
./toolkit_manager.sh show                                       # layers and components
./toolkit_manager.sh install inference --env local              # infrastructure + platform + inference
./toolkit_manager.sh install kserve --env local                 # one component and its depends_on
./toolkit_manager.sh install metallb --only --env local         # exactly that component
./toolkit_manager.sh validate inference --env local             # read-only health checks
./toolkit_manager.sh status --env local                         # what is installed
./toolkit_manager.sh teardown erag --env local                  # a layer and everything above it
./toolkit_manager.sh teardown infrastructure --env local        # everything, cluster included (asks first)
./toolkit_manager.sh install kserve --env local -- -vvv         # pass flags to ansible-playbook
```

---

## Changing a component's version

Versions are role defaults, so you change one by setting the variable in
`env/<name>/global_config.yaml` and installing that component again. For example, for
cert-manager:

1. Add the line to `env/<name>/global_config.yaml`:
   ```yaml
   certmanager_version: "<new version>"   # the role default is in roles/cert_manager/defaults/main.yaml
   ```
2. Re-run just that component:
   ```bash
   ./toolkit_manager.sh install cert_manager --only --env <name>
   ```

Some components have a release switch instead of a free version. For Envoy Gateway (and the
KServe release that matches it), set `envoy_gateway_upgrade: true`. To upgrade a whole layer
to a newer release, follow [Upgrade a Layer](../deploy/upgrade.md).

---

## Extending it

### Adding a component

1. Create the role under `roles/` (here, or in a solution repo):
   ```
   roles/my_component/
   ├── defaults/main.yaml     # every variable it reads, with a working default
   └── tasks/
       ├── main.yaml          # the standard dispatcher
       ├── install.yaml
       ├── teardown.yaml
       └── validate.yaml
   ```
2. Add it to `components.yaml`:
   ```yaml
   components:
     - name: my_component
       layer: platform
       enabled: "{{ my_component_enabled | default(true) | bool }}"
       depends_on: [cert_manager]
   ```
3. Check it with `./toolkit_manager.sh show`, then install it.

The full contract (fields, file rules, ownership labels) is in
[Adding a Solution](adding_solutions.md).

### Adding a solution layer

See [Adding a Solution](adding_solutions.md). In short: add `configs/repos/repos.<layer>.yaml`
here; in your repo add `components.yaml`, `config.yaml` and the roles; then
`./toolkit_manager.sh init <layer>`.

### Adding a model

Add an entry to `env/<name>/models.yaml` (or to the catalog in the inference repo, so every
new env gets it) and deploy it with `./model-manager deploy <name>`.

### Adding an action

1. Accept the action in `parse_args` and `main` in `toolkit_manager.sh`.
2. Add it to `VALID_ACTIONS` in `filter_plugins/resolve_components.py`.
3. Allow it in the dispatcher of each role that supports it (the `component_action` check),
   and add `tasks/<action>.yaml` to those roles.

---

## Safety features

| Feature | How |
|---|---|
| **Stop on the first error** | `any_errors_fatal = true` in `ansible.cfg` |
| **Idempotent** | A second `install` with no change makes no change ([role contract](adding_solutions.md#4-roles)) |
| **Dry run** | `-- --check` passes Ansible's check mode through |
| **Confirmation** | Teardown asks first and names what it removes; `--force` skips the prompt |
| **Teardown removes only what install made** | Ownership labels ([details](adding_solutions.md#ownership-labels)) |
| **Upgrade checks** | Before an upgrade changes anything ([Upgrade a Layer](../deploy/upgrade.md)) |
| **Selective runs** | `--only` runs one target without its dependencies; `--skip` leaves components out |
| **Validation** | `validate` runs each role's read-only checks |
| **Timing** | The `timer` and `profile_tasks` callbacks print how long each task took |

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Walk through installing the layers described here, step by step | [Deployment Guide](../deploy/install_platform.md) |
| See how a request actually flows through the gateway and inference layers | [Network Architecture](../deploy/networking.md) |
| Look up every setting that drives this architecture | [Configuration Reference](../customize/configuration.md) |
| Connect your own app or framework to the inference layer | [Integration Guide](../customize/integration.md) |
| Grow or shrink a cluster after it's already provisioned | [Node Scaling](../customize/node_scaling.md) |
