# CLI Reference

[← Docs Index](../README.md)

All `toolkit_manager.sh` and `model-manager` commands for Toolkit Manager run from the repo root.

```
./toolkit_manager.sh <action> [target] [--env <name>] [options]
```

`--env` defaults to `local` when not specified.

> **Former names:** `toolkit_manager.sh` was called `es_auto_installer.sh`, and `TKM_LOG_LEVEL` / `TKM_ENV` were `ES_LOG_LEVEL` / `ES_ENV`. The old names still work, so existing scripts and pipelines keep running; new ones should use the names on this page.

---

## Actions

### `configure`

One-time machine setup. Installs Python 3.11+, yq, kubectl, and helm into `/usr/local/bin`. Skips tools already present. Requires sudo. Run once per machine.

```bash
./toolkit_manager.sh configure
```

---

### `init <layer>`

Create a new environment directory and seed it with configuration for the specified layer. The layer name (e.g., `inference`, `erag`, `agent-toolkit`) determines which repositories are cloned and which configs are seeded.

```bash
./toolkit_manager.sh init inference                    # standard inference stack
./toolkit_manager.sh init erag                         # inference + Intel® Chatbot Toolkit (default chatqna flavour)
./toolkit_manager.sh init erag --flavour docsum        # Intel® Chatbot Toolkit with docsum pipeline preset
./toolkit_manager.sh init agent-toolkit                # inference + Intel® Agent Toolkit
./toolkit_manager.sh init erag --env prod              # seed env/prod/ instead of env/local/
./toolkit_manager.sh init inference --upgrade          # move already-cloned repos to pinned revs
```

Creates:
- `env/<name>/global_config.yaml` — edit this before installing
- `env/<name>/nodes.yaml` — node IPs and SSH credentials (edit for multi-node)
- `env/<name>/inventory/hosts.yaml` — targets localhost by default; edit for multi-node
- `env/<name>/config.<layer>.yaml` — layer-specific config (e.g., `config.inference.yaml`, `config.erag.yaml`, `config.agent-toolkit.yaml`)
- `env/<name>/models.yaml` — model catalog, pre-seeded from the layer's model_catalog
- `env/<name>/.solutions.yaml` — records which layers this env was inited for

---

### `install`

Deploy components. Dependencies are resolved automatically.

```bash
# Infrastructure + platform + inference (via dependency auto-pull)
./toolkit_manager.sh install inference --env local

# A single layer
./toolkit_manager.sh install platform --env local
./toolkit_manager.sh install inference --env local

# A single component (with auto-pulled dependencies)
./toolkit_manager.sh install kserve --env local

# A single component, skipping dependencies
./toolkit_manager.sh install metallb --only --env local

# Opt-in Intel® Chatbot Toolkit (requires init erag first)
./toolkit_manager.sh install erag --env local

# Opt-in Intel® Agent Toolkit (requires init agent-toolkit first)
./toolkit_manager.sh install agent-toolkit --env local

# Override a config value at runtime (no file edit needed)
./toolkit_manager.sh install kserve --env local -- -e kserve_version=0.15.0

# Dry run — show what would happen without making changes
./toolkit_manager.sh install inference --env local -- --check

# Pass additional Ansible flags (use -- to separate)
./toolkit_manager.sh install inference --env local -- -vvv
```

**Targets for `install` / `teardown`:**

| Target | What it covers |
|---|---|
| `infrastructure` | kubernetes, storage, kata_containers (opt-in, `kata_containers_enabled`) |
| `platform` | nri_cpu_balloons, cert_manager, istio, metallb, envoy_gateway, postgresql, keycloak, object_store, seaweedfs, observability |
| `inference` | keycloak_config, envoy_ai_gateway, kserve, litellm, langfuse, llm_services |
| `erag` | app_inference_models, app_pre_install, app_vector_databases, app_keycloak_config, app_apisix, app_chat_history, app_nats, app_fingerprint, app_hpa, app_pipeline, app_edp, app_mcp_gateway, app_ui, app_watcher, app_post_install (opt-in, from ext repo) |
| `agent-toolkit` | redis_stack, pgvector, keycloak_agent_config, agent_sandbox, openshell, kuberay (opt-in, from ext repo; openshell and kuberay are off by default) |
| `<component>` | Any individual component name (e.g. `kserve`, `grafana`, `metallb`) |

---

### `teardown`

Remove components in reverse dependency order. Configuration files and environment state are preserved.

Teardown deletes what `install` labelled with `ai-solutions.intel.com/env` and `ai-solutions.intel.com/component`, and nothing else (see [Ownership labels](../reference/adding_solutions.md#ownership-labels)). The cluster itself and host changes (`kubernetes`, `storage`, rack-scale networking) are removed by their roles. In an env installed before ownership labels existed, each component's own teardown runs as well, until it has run once (see the link above). `ownership_enabled: false` in `global_config.yaml` runs only the roles' own teardowns.

```bash
# Remove everything (cluster included)
./toolkit_manager.sh teardown infrastructure --env local

# Remove a single component
./toolkit_manager.sh teardown keycloak --env local

# Remove the Intel® Chatbot Toolkit (keeps platform and inference)
./toolkit_manager.sh teardown erag --env local

# Remove a layer but skip dependencies (e.g., tear down cluster without uninstalling erag first)
./toolkit_manager.sh teardown infrastructure --skip erag --env local
```

---

### `validate`

Run post-install health checks.

```bash
./toolkit_manager.sh validate inference --env local
./toolkit_manager.sh validate kserve --env local
./toolkit_manager.sh validate erag --env local
```

Checks are implemented per-component as `tasks/validate.yaml` (asserts, connectivity, replica counts).

---

### `add-node`

Scale worker node(s) into an already-provisioned cluster. No node name is passed — edit `nodes.yaml` first, and the new node(s) are found by diffing the rendered inventory against the live cluster.

```bash
# 1. Add an entry to nodes_workers in env/<name>/nodes.yaml, then:
./toolkit_manager.sh add-node --dry-run --env local   # preview
./toolkit_manager.sh add-node --env local              # apply
```

Control-plane node additions are refused. See [Node Scaling](node_scaling.md). On a rack-scale env, edit `rack-profile.yaml` instead of `nodes.yaml`: see [Rack-scale](node_scaling.md#rack-scale).

---

### `remove-node`

Remove worker node(s) from an already-provisioned cluster. As with `add-node`, no node name is passed on the command line — delete the entry from `nodes_workers` in `nodes.yaml` first, and the removed node(s) are found the same way (diffing `nodes.yaml` against the live cluster), just in the opposite direction: nodes present in the cluster but missing from `nodes.yaml`.

```bash
./toolkit_manager.sh remove-node --dry-run --env local
./toolkit_manager.sh remove-node --env local
./toolkit_manager.sh remove-node --node-lost --env local   # node already NotReady: drain given up after 60 s, no reset
```

Removing a control-plane node is refused — see [Node Scaling](node_scaling.md#control-plane-nodes-are-not-removed). Both `add-node` and `remove-node` refuse on a cluster with ERAG deployed. Before anything runs, a preflight simulates capacity, required anti-affinity/topology spread, node-local volumes, and whether the node is the last one able to run a given workload (e.g. the only GPU node) — no flag skips these, and a check that can't run refuses the removal. After the node is gone, it runs the same checks as `status --health` and fails the command on a problem the removal introduced. See [Node Scaling](node_scaling.md); on a rack-scale env, [Rack-scale](node_scaling.md#rack-scale).

---

### `status`

Print a component status table.

```bash
./toolkit_manager.sh status --env local
```

`status --health` prints a green/red verdict instead (node Readiness, no crash-looping/image-pull-failing/unschedulable pods cluster-wide) and exits with that verdict:

```bash
./toolkit_manager.sh status --health --env local
```

---

### `show`

List all available layers and components, including which are opt-in.

```bash
./toolkit_manager.sh show
```

---

## Flags

| Flag | Description |
|---|---|
| `--env <name>` | Target environment (default: `local`) |
| `--flavour <name>` | (init only) Select a pipeline preset for the layer being inited (e.g., `chatqna`, `docsum`, `audioqna`) |
| `--upgrade` | (init only) Move already-cloned ext/ repos onto the revs the manifests pin now. Refuses on local changes; never rewrites your configs. |
| `--only` | Skip dependency auto-inclusion — run the named target alone |
| `--skip <names>` | Comma-separated layers or components to leave out of the plan (e.g., `--skip erag` to tear down the cluster without uninstalling erag first) |
| `--force` | Skip the confirmation prompt (required in CI, where there is no terminal to answer it) |
| `--dry-run` | (`add-node`/`remove-node` only) print the computed node diff and Kubespray command, then exit |
| `--node-lost` | (`remove-node` only) for a node that is already `NotReady`: no PDB check, the drain given up after 60 s, no reset; refused if any target is still `Ready` |
| `--health` | (`status` only) print a green/red cluster health verdict and exit with it, instead of the full component-status table |
| `-- <ansible-flags>` | Pass remaining args directly to `ansible-playbook` (e.g. `-- -vvv`, `-- --check`, `-- -e key=value`) |

---

## Environment variables

| Variable | Purpose |
|---|---|
| `HF_TOKEN` | Hugging Face token for gated models (Llama, Mistral, etc.) |
| `KEYCLOAK_ADMIN_PASSWORD` | Set Keycloak admin password before install (auto-generated if unset) |
| `GRAFANA_ADMIN_PASSWORD` | Set Grafana admin password before install (auto-generated if unset) |
| `KUBECONFIG` | Override kubeconfig path (auto-detected from `env/<name>/kubeconfig.yaml`) |

---

## model-manager

```
./model-manager <command> [options] [--env <name>]
```

| Command | Description |
|---|---|
| `list` | List models in the catalog |
| `deploy <name>` | Download weights and start serving (from catalog) |
| `deploy --id <hf/repo>` | Deploy any Hugging Face model ad-hoc |
| `undeploy <name>` | Stop serving (weights stay on PVC) |
| `undeploy all` | Stop all models |
| `status` | Show running models and their endpoints |

Key flags for `deploy`:

| Flag | Description |
|---|---|
| `--cpu <n>` | CPU cores |
| `--memory <Gi>` | Memory limit |
| `--replicas <n>` | Number of serving replicas |
| `--tp <n>` | Tensor parallelism width (1, 2, 4, or 8) |
| `--runtime vllm\|openvino` | Serving runtime |
| `--wait` | Block until the model is ready |
| `--dry-run` | Print manifest without applying |
| `--skip-download` | Skip weight download (use existing PVC data) |

See [Deploy a Model](../deploy/deploy_models.md) for usage examples.

---

## Logs

All Ansible output is captured per environment:

| File | Contents |
|---|---|
| `env/<name>/logs/install-all-<timestamp>.log` | Full install output |
| `env/<name>/logs/teardown-all-<timestamp>.log` | Full teardown output |
| `env/<name>/logs/install-<component>-<timestamp>.log` | Per-component install output |
| `env/<name>/logs/ai-solutions-ca.crt` | Self-signed CA certificate (import to browser) |

For more Ansible detail, append `-- -vvv` to any command.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Look up what each `global_config.yaml` field controls | [Configuration Reference](configuration.md) |
| Deploy, access, and manage LLM inference with `model-manager` | [Deploy an LLM](../deploy/deploy_models.md) |
| Set up multi-node or bring-your-own-cluster installs | [Deployment Guide](../deploy/install_platform.md) |
| Add or remove nodes from an already-provisioned cluster | [Node Scaling](node_scaling.md) |
| See the full command dispatch flow this CLI triggers | [Architecture & Design Document](../reference/architecture.md) |
