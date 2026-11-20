# CLI Reference

[← Docs Index](../README.md)

Toolkit Manager has three command-line tools. Run all of them from the root of the
repository (the folder that holds `toolkit_manager.sh`).

| Tool | What it does |
|---|---|
| `./toolkit_manager.sh` | Sets up the machine, creates environments, installs and removes the stack |
| `./run_backup.sh` | Backs up, restores and lists the backups of one layer |
| `./model-manager` | Deploys and removes AI models on a running cluster |

> **Former names:** `toolkit_manager.sh` was called `es_auto_installer.sh`, and `TKM_LOG_LEVEL` / `TKM_ENV` were `ES_LOG_LEVEL` / `ES_ENV`. The old names still work, so existing scripts and pipelines keep running; new ones should use the names on this page.

---

## toolkit_manager.sh

```
./toolkit_manager.sh <action> [target] [--env <name>] [options] [-- ansible-playbook args]
```

- **action**: what to do, for example `install`.
- **target**: what to do it to: a layer (such as `inference`) or a single component (such as
  `kserve`). Only `install`, `teardown` and `validate` take a target.
- **`--env <name>`**: which environment (`env/<name>/`) to act on. The default is `local`.
- Anything after a bare `--` goes straight to `ansible-playbook`.

Options can come in any order. `./toolkit_manager.sh install --env prod platform` works. An option
the action does not accept is refused with an error, never silently ignored.

### The usual order

1. `configure` — once per machine.
2. `init <layer>` — once per environment.
3. Edit `env/<name>/global_config.yaml` if you need to (see [Configuration Reference](configuration.md)).
4. `install <layer>` — deploy.
5. `status` and `validate <layer>` — check it.

### All actions at a glance

| Action | Takes a target | What it does |
|---|---|---|
| [`configure`](#configure) | no | Installs the tools the installer needs on this machine |
| [`init <layer>`](#init-layer) | a layer | Creates an environment and its config files |
| [`show`](#show) | no | Lists every layer and component |
| [`install <target>`](#install) | yes | Deploys a layer or component |
| [`teardown <target>`](#teardown) | yes | Removes a layer or component |
| [`validate <target>`](#validate) | yes | Runs health checks on what is installed |
| [`status`](#status) | no | Shows what is installed |
| [`add-node`](#add-node) | no | Adds worker nodes you added to `nodes.yaml` |
| [`remove-node`](#remove-node) | no | Removes worker nodes you deleted from `nodes.yaml` |

---

### `configure`

Prepares this machine to run the installer. Run it once per machine; skip it if the tools below
are already installed.

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

Creates an environment: a folder `env/<name>/` that holds everything one deployment needs. It also
downloads (clones) the repositories the layer comes from into `ext/`.

```bash
./toolkit_manager.sh init inference                    # standard inference stack
./toolkit_manager.sh init erag                         # inference + Intel® Chatbot Toolkit (default chatqna flavour)
./toolkit_manager.sh init erag --flavour docsum        # Intel® Chatbot Toolkit with the docsum pipeline preset
./toolkit_manager.sh init agent-toolkit                # inference + Intel® Agent Toolkit
./toolkit_manager.sh init erag --env prod              # create env/prod/ instead of env/local/
./toolkit_manager.sh init inference --upgrade          # move already-cloned repos to the revisions pinned now
```

The layers you can pass are the ones with a manifest in `configs/repos/` (`inference`, `erag`,
`agent-toolkit`); an unknown name is refused with the list of valid ones.

What it creates (a file that already exists is never overwritten; you see `exists, skipping`):

| File | What it is |
|---|---|
| `env/<name>/global_config.yaml` | Main settings. Edit this before installing |
| `env/<name>/nodes.yaml` | The machines to install on (IPs, SSH user and key). Edit it for more than one node |
| `env/<name>/inventory/hosts.yaml` | Ansible inventory; points at this machine by default |
| `env/<name>/config.<layer>.yaml` | One per layer the environment holds (for example `config.inference.yaml`, `config.erag.yaml`) |
| `env/<name>/models.yaml` | The model catalog `model-manager` reads, copied from the inference repository |
| `env/<name>/.solutions.yaml` | A record of which layers this environment was created for. Do not edit it |
| `env/<name>/logs/` | Where every later command writes its log |

Running `init` again for another layer adds that layer to the same environment. `--flavour` picks a
preset for the layer you name; once a config file was seeded from one flavour, asking for another
is refused (it would throw away your edits): use a new `--env`, or delete that
`config.<layer>.yaml` to reseed it.

`--upgrade` is for moving to a newer release: it moves the repositories in `ext/` to the revisions
the manifests pin now. It refuses if a repository has uncommitted changes, and never rewrites your
config files: it only reports what changed upstream, so you can copy what you need.

---

### `show`

Lists every layer and the components in it. Layers that are off until you install them are marked
`(opt-in: install <layer>)`.

```bash
./toolkit_manager.sh show
```

---

### `install`

Deploys a layer or a component, and whatever it depends on.

```bash
# Infrastructure + platform + inference (dependencies are pulled in automatically)
./toolkit_manager.sh install inference --env local

# A single layer (and the layers below it, if they are not there yet)
./toolkit_manager.sh install platform --env local

# A single component, with the components it needs
./toolkit_manager.sh install kserve --env local

# A single component on its own, nothing else
./toolkit_manager.sh install metallb --only --env local

# Intel® Chatbot Toolkit (run `init erag` first)
./toolkit_manager.sh install erag --env local

# Intel® Agent Toolkit (run `init agent-toolkit` first)
./toolkit_manager.sh install agent-toolkit --env local

# Change a setting for this run only, without editing a file
./toolkit_manager.sh install inference --env local -- -e routing_mode=path

# Show more Ansible detail
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
in line with your config.

Installing a newer release over a deployed layer upgrades it. Before anything changes, `install`
prints each layer's run class (`fresh`, `reinstall`, `resume`, `upgrade`, `downgrade`, `conflict`,
`unversioned`) and the verdict of each upgrade check; a layer that declares `supports.upgrade`
refuses a blocking verdict. See [Upgrade a Layer](../deploy/upgrade.md).

**Targets for `install`, `teardown` and `validate`:**

| Target | What it covers |
|---|---|
| `infrastructure` | `kubernetes`, `storage`; plus `kata_containers` (`kata_containers_enabled`) and the `fabric_*` rack-scale networking components (`rack_scale_enabled`) when switched on |
| `platform` | `cert_manager`, `istio`, `metallb`, `gateway` (also accepted as `envoy_gateway`), `postgresql`, `keycloak`, `object_store`, `seaweedfs`, `observability`, `metrics_server`, `node_feature_discovery`, `nri_cpu_balloons`; plus `headlamp`, `keda` and `velero` when switched on, and `nvidia_gpu_operator` / `gpu_observability` on rack-scale |
| `inference` | `keycloak_config`, `envoy_ai_gateway`, `kserve`, `llm_services`; plus `litellm` and `langfuse` (with `auth_type: litellm_vkey` or `litellm_enabled: true`), `agentgateway` and `vllm_sr` when switched on. Comes from the inference repository |
| `erag` | The Intel® Chatbot Toolkit components (vector databases, document ingestion, chat UI, …). Comes from its repository; opt-in |
| `agent-toolkit` | The Intel® Agent Toolkit components (agent sandboxes, memory stores, …). Comes from its repository; opt-in |
| `<component>` | Any single component name, for example `kserve`, `observability` or `metallb` |

Some components are skipped on OpenShift (for example `istio`, `metallb`, `headlamp`). Run
`./toolkit_manager.sh show` for the exact list your checkout and environment hold.

---

### `teardown`

Removes a layer or a component, newest first. Your files in `env/<name>/` stay; only what is
running in the cluster is removed.

```bash
# Remove everything, including the Kubernetes cluster
./toolkit_manager.sh teardown infrastructure --env local

# Remove the Intel® Chatbot Toolkit (keeps platform and inference)
./toolkit_manager.sh teardown erag --env local

# Remove Keycloak and everything that depends on it
./toolkit_manager.sh teardown keycloak --env local

# Remove only Keycloak, nothing that depends on it
./toolkit_manager.sh teardown keycloak --only --env local

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

Teardown deletes what `install` labelled with `ai-solutions.intel.com/env` and `ai-solutions.intel.com/component`, and nothing else (see [Ownership labels](../reference/adding_solutions.md#ownership-labels)). The cluster itself and host changes (`kubernetes`, `storage`, rack-scale networking) are removed by their roles. In an env installed before ownership labels existed, each component's own teardown runs as well, until it has run once (see the link above). `ownership_enabled: false` in `global_config.yaml` runs only the roles' own teardowns.

---

### `validate`

Runs the health checks of each component in the target: deployments ready, endpoints answering,
expected objects present. It changes nothing.

```bash
./toolkit_manager.sh validate inference --env local
./toolkit_manager.sh validate kserve --env local
./toolkit_manager.sh validate erag --env local
```

A failed check stops the run with the reason. The checks live in each component's
`tasks/validate.yaml`.

---

### `add-node`

Adds worker nodes to a cluster that is already running. You do not name the node on the command
line: you add it to `nodes.yaml`, and the installer finds what is new.

1. Add an entry under `nodes_workers` in `env/<name>/nodes.yaml`.
2. Preview: `./toolkit_manager.sh add-node --dry-run --env local`
3. Apply: `./toolkit_manager.sh add-node --env local` (asks before it runs Kubespray).

Adding a control-plane node is refused. On a rack-scale env, edit `rack-profile.yaml` instead of
`nodes.yaml`: see [Rack-scale](node_scaling.md#rack-scale). Full guide: [Node Scaling](node_scaling.md).

---

### `remove-node`

Removes worker nodes from a cluster that is already running. As with `add-node`, you delete the
node from `nodes_workers` in `nodes.yaml`, and the installer removes the nodes that are in the
cluster but no longer in the file.

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
Intel® Chatbot Toolkit deployed. On a rack-scale env, see [Rack-scale](node_scaling.md#rack-scale).

---

### `status`

Shows what is installed: namespaces, pods, Helm releases and service addresses.

```bash
./toolkit_manager.sh status --env local
```

With no cluster yet, it says nothing is installed and exits.

`--health` prints a short `green` or `red` verdict instead: every node `Ready`, and no pod
crash-looping, failing to pull its image or unschedulable. It exits `0` for green and `1` for red,
so scripts can use it:

```bash
./toolkit_manager.sh status --health --env local
```

---

## Options

| Option | Works with | What it does |
|---|---|---|
| `--env <name>` | every action except `configure` and `show` | The environment to act on, `env/<name>/` (default: `local`) |
| `--flavour <name>` | `init` | The preset for the layer being created (for example `chatqna`, `docsum`) |
| `--upgrade` | `init` | Move already-cloned `ext/` repositories onto the revisions the manifests pin now. Refuses on local changes; never rewrites your configs |
| `--only` | `install`, `teardown`, `validate` | Act on the target alone, without the components it depends on (install) or that depend on it (teardown) |
| `--skip <names>` | `install`, `teardown`, `validate` | Comma-separated layers or components to leave out (for example `--skip erag`) |
| `--force` | `install`, `teardown`, `add-node`, `remove-node` | Answer yes to the confirmation prompt (needed in CI). On `install`, also run past a refusing upgrade check; the run is recorded as forced |
| `--dry-run` | `add-node`, `remove-node` | Print the node changes and the Kubespray command, then exit. `remove-node` also prints where each pod would move |
| `--node-lost` | `remove-node` | For a node that is already `NotReady`: no PodDisruptionBudget check, the drain is given up after 60 s, and the node is not reset over SSH. The capacity check still runs. Refused if the node is still `Ready` |
| `--health` | `status` | Print a green/red verdict and exit with it |
| `-h`, `--help` | — | Print the built-in help |
| `-v`, `--version` | — | Print the installer version |
| `-- <args>` | `install`, `teardown`, `validate`, `status` | Pass the rest to `ansible-playbook` (for example `-- -vvv`, `-- -e key=value`) |

---

## Environment variables

| Variable | Read by | What it does |
|---|---|---|
| `TKM_LOG_LEVEL` | `toolkit_manager.sh`, `run_backup.sh` | `info` (default), `debug` (more output and `ansible-playbook -vvv`) or `trace` (also shell tracing and `ANSIBLE_DEBUG`) |
| `TKM_ENV` | `./model-manager` | The environment to use, for example `TKM_ENV=prod ./model-manager list`. Not needed when there is only one environment |
| `HF_TOKEN` | `./model-manager`, `install` (models it deploys) | Hugging Face token, needed for gated models such as Llama |

The installer sets `KUBECONFIG` itself, to `env/<name>/kubeconfig.yaml`, or to the
`existing_kubernetes` path for your own cluster. A `KUBECONFIG` set in your shell is not used. To
point it at your own cluster, set `existing_kubernetes` (see [BYO cluster](configuration.md#byo-cluster)).

The Keycloak and Grafana admin passwords are not taken from the environment. See
[Admin passwords](configuration.md#admin-passwords).

---

## run_backup.sh

Backs up and restores one layer with Velero. It takes the same `--env` and `--` options as
`toolkit_manager.sh`, and needs Velero installed (`velero_enabled: true`).

```bash
./run_backup.sh backup  <layer> [--env <name>] [--force] [-- ansible-playbook args]
./run_backup.sh restore <layer> [--env <name>] [--from <backup>] [--force] [-- args]
./run_backup.sh list    <layer> [--env <name>] [-- args]
```

| Option | Works with | What it does |
|---|---|---|
| `--env <name>` | all | The environment (default: `local`) |
| `--from <backup>` | `restore` | Restore this backup instead of the one picked automatically (`list` marks that one) |
| `--force` | `restore` | Answer yes to the confirmation prompt (needed where there is no terminal) |
| `--force` | `backup` | Take the backup even though the checkout is at another version than the one installed. Such a backup is labelled forced: `restore` uses it only when you name it with `--from` |

`restore` deletes data before it replays the backup, so it always asks first. See
[Upgrade a Layer](../deploy/upgrade.md#backup-and-restore) for the full procedure. Its log is
`env/<name>/logs/backup-<layer>-<timestamp>.log`.

---

## model-manager

Deploys and removes models on a running cluster. It works on the environment named by `TKM_ENV`;
with only one environment it picks that one, and with several it asks (or, with no terminal, stops
and asks you to set `TKM_ENV`).

```
./model-manager <command> [options]
```

| Command | What it does |
|---|---|
| `list` | The catalog (`env/<name>/models.yaml`) with each model's status, plus deployed models that are not in it. `--catalog` shows only the catalog, `--deployed` only what is running |
| `deploy <name>` | Download a catalog model's weights and start serving it |
| `deploy --id <hf/repo>` | Deploy any Hugging Face model that is not in the catalog |
| `undeploy <name>` | Stop serving a model. The downloaded weights stay, so a later deploy is quick |
| `undeploy all` | Stop every model |
| `cpu-collisions-check` | Report CPU pinning and NUMA layout, and flag models sharing cores |

Main options for `deploy`:

| Option | What it does |
|---|---|
| `--server vllm\|openvino` | The serving engine (default `vllm`; `--runtime` is accepted too) |
| `--cpu <n>` | CPU cores |
| `--memory <n>Gi` | Memory, for example `32Gi` |
| `--replicas <n>` | Number of copies serving the model (default 1) |
| `--tp <n>` | Tensor parallelism: split the model across `n` NUMA nodes |
| `--as <name>` | Deploy under another name, to run two variants of one model side by side |
| `--node <name>` | Run it on this node |
| `--env KEY=VAL` | Set an environment variable in the model's container (repeatable). This is **not** the environment name: use `TKM_ENV` for that |
| `--wait` | Wait until the model is ready (`--wait-timeout <s>`, default 900) |
| `--dry-run` | Print the manifest without applying it |

`./model-manager --help` lists every option. See [Deploy a Model](../deploy/deploy_models.md) for
step-by-step examples.

---

## Logs

Every `toolkit_manager.sh` run (except `configure`, `init` and `show`) writes a log file in
`env/<name>/logs/`. The installer keeps the 30 most recent.

| File | Contents |
|---|---|
| `env/<name>/logs/<action>-<target>-<YYYYMMDD-HHMMSS>.log` | One run, for example `install-inference-20261120-091522.log`. `status`, `add-node` and `remove-node` use `cluster` as the target |
| `env/<name>/logs/backup-<layer>-<YYYYMMDD-HHMMSS>.log` | One `run_backup.sh` run |
| `env/<name>/logs/ai-solutions-ca.crt` | The self-signed CA certificate (import it into your browser; see [TLS](configuration.md#tls)) |
| `env/<name>/reports/<layer>-upgrade-report-<UTC>.yaml` | One per layer of each install that upgrades or downgrades it |

When a run fails, the last lines on screen name its log. For more detail, add `-- -vvv`, or set
`TKM_LOG_LEVEL=debug`.

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
