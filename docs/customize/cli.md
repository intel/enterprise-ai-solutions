# CLI Reference

[← Docs Index](../README.md)

All `toolkit_manager.sh` and `model-manager` commands for Toolkit Manager run from the repo root
(the folder that holds `toolkit_manager.sh`). A third script, `run_backup.sh`, backs up and
restores one layer; it is described at the end of [Actions](#actions).

```
./toolkit_manager.sh <action> [target] [--env <name>] [options] [-- ansible-playbook args]
```

- **action**: what to do, for example `install`.
- **target**: what to do it to: a layer (such as `inference`) or a single component (such as
  `kserve`). Only `install`, `teardown` and `validate` take a target.
- Anything after a bare `--` goes straight to `ansible-playbook`.

`--env` defaults to `local` when not specified. Options can come in any order
(`./toolkit_manager.sh install --env prod platform` works). An option the action does not accept
is refused with an error, never silently ignored.

> **Former names:** `toolkit_manager.sh` was called `es_auto_installer.sh`, and `TKM_LOG_LEVEL` / `TKM_ENV` were `ES_LOG_LEVEL` / `ES_ENV`. The old names still work, so existing scripts and pipelines keep running; new ones should use the names on this page.

---

## Actions

The usual order:

1. `configure` — once per machine.
2. `init <layer>` — once per environment.
3. Edit `env/<name>/global_config.yaml` if you need to (see [Configuration Reference](configuration.md)).
4. `install <layer>` — deploy.
5. `status` and `validate <layer>` — check it.

| Action | Takes a target | What it does |
|---|---|---|
| [`configure`](#configure) | no | Installs the tools the installer needs on this machine |
| [`init <layer>`](#init-layer) | a layer | Creates an environment and its config files |
| [`install <target>`](#install) | yes | Deploys a layer or component |
| [`teardown <target>`](#teardown) | yes | Removes a layer or component |
| [`validate <target>`](#validate) | yes | Runs health checks on what is installed |
| [`add-node`](#add-node) | no | Adds worker nodes you added to `nodes.yaml` |
| [`remove-node`](#remove-node) | no | Removes worker nodes you deleted from `nodes.yaml` |
| [`status`](#status) | no | Shows what is installed |
| [`show`](#show) | no | Lists every layer and component |

### `configure`

One-time machine setup. Run it once per machine; skip it if the tools below are already installed.

```bash
./toolkit_manager.sh configure
```

It makes **permanent** changes, using `sudo`:

- installs Python 3.11 or newer and its `venv` package (Ubuntu 22.04 uses the deadsnakes PPA;
  Ubuntu 24.04 and RHEL/Rocky use their own packages),
- installs the `en_US.UTF-8` locale,
- installs `yq`, `kubectl` and `helm` into `/usr/local/bin`. A tool that is already installed is
  left as it is, with a warning to check its version,
- creates the installer's Python environment in `.venv/`.

It lists these changes and asks `Proceed? [y/N]` first, so it needs a terminal. It takes no options
(not even `--force`). Supported systems: Ubuntu 22.04 and 24.04, and RHEL-family systems (RHEL,
Rocky, CentOS, AlmaLinux, Fedora). On anything else it stops and asks you to install the tools by
hand.

---

### `init <layer>`

Create a new environment directory (`env/<name>/`, everything one deployment needs) and seed it
with configuration for the specified layer. It also downloads (clones) the repositories the layer
comes from into `ext/`. The layer name (`inference`, `erag`, `agent-toolkit`: the ones with a
manifest in `configs/repos/`) determines which repositories are cloned and which configs are
seeded; an unknown name is refused with the list of valid ones.

```bash
./toolkit_manager.sh init inference                    # standard inference stack
./toolkit_manager.sh init erag                         # inference + Intel® Chatbot Toolkit (default chatqna flavour)
./toolkit_manager.sh init erag --flavour docsum        # Intel® Chatbot Toolkit with the docsum pipeline preset
./toolkit_manager.sh init agent-toolkit                # inference + Intel® Agent Toolkit
./toolkit_manager.sh init erag --env prod              # create env/prod/ instead of env/local/
./toolkit_manager.sh init inference --upgrade          # move already-cloned repos to the revisions pinned now
```

Creates (a file that already exists is never overwritten; you see `exists, skipping`):
- `env/<name>/global_config.yaml` — main settings; edit this before installing
- `env/<name>/nodes.yaml` — the machines to install on (IPs, SSH user and key); edit it for more than one node
- `env/<name>/inventory/hosts.yaml` — Ansible inventory; points at this machine by default
- `env/<name>/config.<layer>.yaml` — one per layer the environment holds (for example `config.inference.yaml`, `config.erag.yaml`, `config.agent-toolkit.yaml`)
- `env/<name>/models.yaml` — the model catalog `model-manager` reads, copied from the inference repository
- `env/<name>/.solutions.yaml` — records which layers this env was created for; do not edit it
- `env/<name>/logs/` — where every later command writes its log

Running `init` again for another layer adds that layer to the same environment. `--flavour` picks a
preset for the layer you name; once a config file was seeded from one flavour, asking for another
is refused (it would throw away your edits): use a new `--env`, or delete that
`config.<layer>.yaml` to reseed it.

`--upgrade` is for moving to a newer release: it moves the repositories in `ext/` to the revisions
the manifests pin now. It refuses if a repository has uncommitted changes, and never rewrites your
config files: it only reports what changed upstream, so you can copy what you need.

---

### `install`

Deploy components. Dependencies are resolved automatically.

```bash
# Infrastructure + platform + inference (dependencies are pulled in automatically)
./toolkit_manager.sh install inference --env local

# A single layer (and the layers below it, if they are not there yet)
./toolkit_manager.sh install platform --env local

# A single component, with the components it needs
./toolkit_manager.sh install kserve --env local

# A single component on its own, nothing else
./toolkit_manager.sh install metallb --only --env local

# Opt-in Intel® Chatbot Toolkit (run `init erag` first)
./toolkit_manager.sh install erag --env local

# Opt-in Intel® Agent Toolkit (run `init agent-toolkit` first)
./toolkit_manager.sh install agent-toolkit --env local

# Change a setting for this run only, without editing a file
./toolkit_manager.sh install inference --env local -- -e routing_mode=path

# Pass additional Ansible flags (use -- to separate)
./toolkit_manager.sh install inference --env local -- -vvv

# Run past a refusing upgrade check (the run is recorded as forced)
./toolkit_manager.sh install erag --env local --force
```

What happens:

1. The installer checks the target exists. A solution layer (`erag`, `agent-toolkit`, …) must have
   been added to this environment with `init` first, or it stops and tells you the `init` command.
2. If there is no cluster yet (no `env/<name>/kubeconfig.yaml`), it builds one with Kubespray
   first. On later runs it finds the cluster and skips that step.
3. It prints the plan (the components it will install, in order) and the upgrade-check lines, then
   installs.
4. On success it prints `INSTALLATION COMPLETE` with the log file's path. Run `status` to see
   the service addresses.

`install` does not ask for confirmation. Running it again is safe: it brings the deployment back
in line with your config. There is no reliable dry run: not every role supports Ansible check
mode (`-- --check`).

Installing a newer release over a deployed layer upgrades it. Before anything changes, `install`
prints each layer's run class (`fresh`, `reinstall`, `resume`, `upgrade`, `downgrade`, `conflict`,
`unversioned`) and the verdict of each upgrade check; a layer that declares `supports.upgrade`
refuses a blocking verdict. See [Upgrade a Layer](../deploy/upgrade.md).

**Targets for `install` / `teardown`:**

| Target | What it covers |
|---|---|
| `infrastructure` | `kubernetes`, `storage`; plus `kata_containers` (`kata_containers_enabled`) and the `fabric_*` rack-scale networking components (`rack_scale_enabled`) when switched on |
| `platform` | `cert_manager`, `istio`, `metallb`, `gateway` (also accepted as `envoy_gateway`), `postgresql`, `keycloak`, `object_store`, `seaweedfs`, `observability`, `metrics_server`, `node_feature_discovery`, `nri_cpu_balloons`; plus `headlamp`, `keda` and `velero` when switched on, and `nvidia_gpu_operator` / `gpu_observability` on rack-scale |
| `inference` | `keycloak_config`, `envoy_ai_gateway`, `kserve`, `llm_services`; plus `litellm` and `langfuse` (with `auth_type: litellm_vkey` or `litellm_enabled: true`), `agentgateway` and `vllm_sr` when switched on. Comes from the inference repository |
| `erag` | The Intel® Chatbot Toolkit components (vector databases, document ingestion, chat UI, …). Comes from its repository; opt-in |
| `agent-toolkit` | The Intel® Agent Toolkit components (agent sandboxes, memory stores, …). Comes from its repository; opt-in |
| `<component>` | Any single component name, for example `kserve`, `observability` or `metallb` |

The same targets work for `validate`. Some components are skipped on OpenShift (for example
`istio`, `metallb`, `headlamp`). Run `./toolkit_manager.sh show` for the exact list your checkout
and environment hold.

---

### `teardown`

Remove components in reverse dependency order (newest first). Configuration files and environment
state in `env/<name>/` are preserved; only what is running in the cluster is removed.

Teardown deletes what `install` labelled with `ai-solutions.intel.com/env` and `ai-solutions.intel.com/component`, and nothing else (see [Ownership labels](../reference/adding_solutions.md#ownership-labels)). The cluster itself and host changes (`kubernetes`, `storage`, rack-scale networking) are removed by their roles. In an env installed before ownership labels existed, each component's own teardown runs as well, until it has run once (see the link above). `ownership_enabled: false` in `global_config.yaml` runs only the roles' own teardowns.

```bash
# Remove everything, including the Kubernetes cluster
./toolkit_manager.sh teardown infrastructure --env local

# Remove Keycloak and everything that depends on it
./toolkit_manager.sh teardown keycloak --env local

# Remove only Keycloak, nothing that depends on it
./toolkit_manager.sh teardown keycloak --only --env local

# Remove the Intel® Chatbot Toolkit (keeps platform and inference)
./toolkit_manager.sh teardown erag --env local

# Destroy the cluster without uninstalling erag from it first
./toolkit_manager.sh teardown infrastructure --skip erag --env local
```

What to expect:

1. It asks `About to tear down '<target>' and everything above it … Proceed? [y/N]`. Pass
   `--force` to answer yes (needed where there is no terminal, such as CI).
2. A target takes everything that depends on it along: tearing down `platform` also removes
   `inference` and any solution layer; tearing down a component removes the components that need
   it. Use `--only` to remove just the target.
3. The Kubernetes cluster is destroyed **only** when you target `infrastructure` or `kubernetes`
   (Kubespray reset). It then also deletes `env/<name>/kubeconfig.yaml`. A bring-your-own cluster
   (`existing_kubernetes`) is never destroyed.

---

### `validate`

Run post-install health checks: deployments ready, endpoints answering, expected objects present.
It changes nothing.

```bash
./toolkit_manager.sh validate inference --env local
./toolkit_manager.sh validate kserve --env local
./toolkit_manager.sh validate erag --env local
```

Checks are implemented per-component as `tasks/validate.yaml` (asserts, connectivity, replica
counts). A failed check stops the run with the reason.

---

### `add-node`

Scale worker node(s) into an already-provisioned cluster. No node name is passed on the command
line: you add the node to `nodes.yaml`, and the installer finds what is new by comparing it with
the live cluster.

```bash
# 1. Add an entry to nodes_workers in env/<name>/nodes.yaml, then:
./toolkit_manager.sh add-node --dry-run --env local   # 2. preview
./toolkit_manager.sh add-node --env local              # 3. apply (asks before it runs Kubespray)
```

Control-plane node additions are refused. See [Node Scaling](node_scaling.md). On a rack-scale env,
edit `rack-profile.yaml` instead of `nodes.yaml`: see [Rack-scale](node_scaling.md#rack-scale).

---

### `remove-node`

Remove worker node(s) from an already-provisioned cluster. As with `add-node`, no node name is
passed on the command line: delete the entry from `nodes_workers` in `nodes.yaml`, and the
installer removes the nodes that are in the cluster but no longer in the file.

```bash
./toolkit_manager.sh remove-node --dry-run --env local     # preview, including where each pod would move
./toolkit_manager.sh remove-node --env local               # apply (asks first)
./toolkit_manager.sh remove-node --node-lost --env local   # the node is already NotReady (dead or unreachable)
```

Removing a control-plane node is refused — see [Node Scaling](node_scaling.md#control-plane-nodes-are-not-removed).
Before anything runs, a check simulates whether the remaining nodes can take the node's work
(capacity, anti-affinity and spread rules, node-local volumes, and whether it is the last node
able to run something, such as the only GPU node). No flag skips this check, and a check that
cannot run refuses the removal. After the node is gone, the same checks as `status --health` run,
and the command fails on any problem the removal caused.

`add-node` and `remove-node` both refuse on a bring-your-own cluster and on a cluster with the
Intel® Chatbot Toolkit deployed. See [Node Scaling](node_scaling.md); on a rack-scale env,
[Rack-scale](node_scaling.md#rack-scale).

---

### `status`

Print a component status table: namespaces, pods, Helm releases and service addresses. With no
cluster yet, it says nothing is installed and exits.

```bash
./toolkit_manager.sh status --env local
```

`status --health` prints a short `green` or `red` verdict instead: every node `Ready`, and no pod
crash-looping, failing to pull its image or unschedulable. It exits `0` for green and `1` for red,
so scripts can use it:

```bash
./toolkit_manager.sh status --health --env local
```

---

### `show`

List all available layers and the components in each. Layers that are off until you install them
are marked `(opt-in: install <layer>)`.

```bash
./toolkit_manager.sh show
```

---

Backing up and restoring a layer are not `toolkit_manager.sh` actions; `run_backup.sh` runs them
with Velero, with the same `--env` and `--` conventions. It needs Velero installed
(`velero_enabled: true`):

```bash
./run_backup.sh backup  <layer> [--env <name>] [--force] [-- ansible-playbook args]
./run_backup.sh restore <layer> [--env <name>] [--from <backup>] [--force] [-- args]
./run_backup.sh list    <layer> [--env <name>] [-- args]
```

| Flag | Description |
|---|---|
| `--env <name>` | Target environment (default: `local`) |
| `--from <backup>` | (`restore` only) restore this backup instead of the one picked automatically (`list` marks that one) |
| `--force` | On `restore`: answer yes to the confirmation prompt (needed where there is no terminal). On `backup`: take the backup even though the checkout is at another version than the one installed; such a backup is labelled forced, and `restore` uses it only when you name it with `--from` |

`restore` deletes data before it replays the backup, so it always asks first; a run with no
terminal and no `--force` is refused. Its log is `env/<name>/logs/backup-<layer>-<timestamp>.log`.
See [Upgrade a Layer](../deploy/upgrade.md#backup-and-restore).

---

## Flags

| Flag | Description |
|---|---|
| `--env <name>` | Target environment, `env/<name>/` (default: `local`). Every action except `configure` and `show` |
| `--flavour <name>` | (init only) Select a preset for the layer being inited (e.g., `chatqna`, `docsum`) |
| `--upgrade` | (init only) Move already-cloned `ext/` repos onto the revisions the manifests pin now. Refuses on local changes; never rewrites your configs |
| `--only` | (`install`, `teardown`, `validate`) Act on the target alone, without the components it depends on (install) or that depend on it (teardown) |
| `--skip <names>` | (`install`, `teardown`, `validate`) Comma-separated layers or components to leave out of the plan (e.g., `--skip erag` to tear down the cluster without uninstalling erag first) |
| `--force` | (`install`, `teardown`, `add-node`, `remove-node`) Skip the confirmation prompt (required in CI, where there is no terminal to answer it). On `install`, also run past a refusing upgrade check, recorded as forced |
| `--dry-run` | (`add-node`/`remove-node` only) Print the computed node changes and the Kubespray command, then exit. `remove-node` also prints where each pod would move |
| `--node-lost` | (`remove-node` only) For a node that is already `NotReady`: no PodDisruptionBudget check, the drain is given up after 60 s, and the node is not reset over SSH. The capacity check still runs. Refused if the node is still `Ready` |
| `--health` | (`status` only) Print a green/red cluster health verdict and exit with it, instead of the full status table |
| `-h`, `--help` | Print the built-in help |
| `-v`, `--version` | Print the installer version |
| `-- <ansible-flags>` | (`install`, `teardown`, `validate`, `status`) Pass remaining args directly to `ansible-playbook` (e.g. `-- -vvv`, `-- -e key=value`) |

---

## Environment variables

| Variable | Purpose |
|---|---|
| `TKM_LOG_LEVEL` | Output detail for `toolkit_manager.sh` and `run_backup.sh`: `info` (default), `debug` (more output and `ansible-playbook -vvv`) or `trace` (also shell tracing and `ANSIBLE_DEBUG`) |
| `TKM_ENV` | The environment `./model-manager` uses, for example `TKM_ENV=prod ./model-manager list`. Not needed when there is only one environment |
| `HF_TOKEN` | Hugging Face token for gated models (such as Llama), read by `./model-manager` and by the models `install` deploys |

The installer sets `KUBECONFIG` itself, to `env/<name>/kubeconfig.yaml`, or to the
`existing_kubernetes` path for your own cluster. A `KUBECONFIG` set in your shell is not used. To
point it at your own cluster, set `existing_kubernetes` (see
[BYO cluster](configuration.md#byo-cluster)).

The Keycloak and Grafana admin passwords are not taken from the environment. See
[Admin passwords](configuration.md#admin-passwords).

---

## model-manager

Deploys and removes models on a running cluster. It works on the environment named by `TKM_ENV`;
with only one environment it picks that one, and with several it asks (or, with no terminal, stops
and asks you to set `TKM_ENV`).

```
./model-manager <command> [options]
```

| Command | Description |
|---|---|
| `list` | The catalog (`env/<name>/models.yaml`) with each model's status, plus deployed models that are not in it. `--catalog` shows only the catalog, `--deployed` only what is running |
| `deploy <name>` | Download a catalog model's weights and start serving it |
| `deploy --hfid <hf/repo>` | Deploy any Hugging Face model that is not in the catalog (`--id` still works, deprecated) |
| `undeploy <name>` | Stop serving a model. The downloaded weights stay on the volume, so a later deploy is quick |
| `undeploy all` | Stop every model |
| `cpu-collisions-check` | Report CPU pinning and NUMA layout, and flag models sharing cores |

Key flags for `deploy`:

| Flag | Description |
|---|---|
| `--server vllm\|openvino\|vllm-cuda` | The serving engine (default `vllm`; `--runtime` is accepted too) |
| `--cpu <n>` | CPU cores |
| `--memory <n>Gi` | Memory, for example `32Gi` |
| `--replicas <n>` | Fixed number of copies serving the model (default 1) |
| `--tp <n>` | Tensor parallelism: split the model across `n` NUMA nodes (on a GPU runtime, also `n` GPUs) |
| `--as <name>` | Deploy under another name, to run two variants of one model side by side |
| `--node <name>` | Pin the model to this node |
| `--env KEY=VAL` | Set an environment variable in the model's container (repeatable). This is **not** the environment name: use `TKM_ENV` for that |
| `--wait` | Wait until the model is ready (`--wait-timeout <s>`, default 900) |
| `--dry-run` | Print the manifest without applying it |

`./model-manager --help` lists every option. See [Deploy a Model](../deploy/deploy_models.md) for
usage examples.

---

## Logs

Every `toolkit_manager.sh` run (except `configure`, `init` and `show`) writes a log file in
`env/<name>/logs/`. The installer keeps the 30 most recent.

| File | Contents |
|---|---|
| `env/<name>/logs/<action>-<target>-<YYYYMMDD-HHMMSS>.log` | One run, for example `install-inference-20261120-091522.log`. `status`, `add-node` and `remove-node` use `cluster` as the target |
| `env/<name>/logs/backup-<layer>-<YYYYMMDD-HHMMSS>.log` | One `run_backup.sh` run |
| `env/<name>/logs/ai-solutions-ca.crt` | Self-signed CA certificate (import it into your browser; see [TLS](configuration.md#tls)) |
| `env/<name>/reports/<layer>-upgrade-report-<UTC>.yaml` | One per layer of each install that upgrades or downgrades it |

When a run fails, the last lines on screen name its log. For more Ansible detail, append `-- -vvv`
to any command, or set `TKM_LOG_LEVEL=debug`.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Look up what each `global_config.yaml` field controls | [Configuration Reference](configuration.md) |
| Upgrade a layer, back it up, restore or roll it back | [Upgrade a Layer](../deploy/upgrade.md) |
| Deploy, access, and manage models with `model-manager` | [Deploy a Model](../deploy/deploy_models.md) |
| Set up multi-node or bring-your-own-cluster installs | [Deployment Guide](../deploy/install_platform.md) |
| Add or remove nodes from an already-provisioned cluster | [Node Scaling](node_scaling.md) |
| See the full command dispatch flow this CLI triggers | [Architecture & Design Document](../reference/architecture.md) |
