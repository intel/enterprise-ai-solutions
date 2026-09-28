# CLI Reference

[← Docs Index](../README.md)

All `es_auto_installer.sh` and `model-manager` commands for Intel® AI for Enterprise Solutions run from the repo root.

```
./es_auto_installer.sh <action> [target] [--env <name>] [options]
```

`--env` defaults to `local` when not specified.

---

## Actions

### `configure`

One-time machine setup. Installs Python 3.11+, yq, kubectl, and helm into `/usr/local/bin`. Skips tools already present. Requires sudo. Run once per machine.

```bash
./es_auto_installer.sh configure
```

---

### `init <layer>`

Create a new environment directory and seed it with configuration for the specified layer. The layer name (e.g., `inference`, `erag`) determines which repositories are cloned and which configs are seeded.

```bash
./es_auto_installer.sh init inference                    # standard inference stack
./es_auto_installer.sh init erag                         # inference + Intel AI for Enterprise RAG (default chatqna flavour)
./es_auto_installer.sh init erag --flavour docsum        # Intel AI for Enterprise RAG with docsum pipeline preset
./es_auto_installer.sh init erag --env prod              # seed env/prod/ instead of env/local/
./es_auto_installer.sh init inference --upgrade          # move already-cloned repos to pinned revs
```

Creates:
- `env/<name>/global_config.yaml` — edit this before installing
- `env/<name>/nodes.yaml` — node IPs and SSH credentials (edit for multi-node)
- `env/<name>/inventory/hosts.yaml` — targets localhost by default; edit for multi-node
- `env/<name>/config.<layer>.yaml` — layer-specific config (e.g., `config.inference.yaml`, `config.erag.yaml`)
- `env/<name>/models.yaml` — model catalog, pre-seeded from the layer's model_catalog
- `env/<name>/.solutions.yaml` — records which layers this env was inited for

---

### `install`

Deploy components. Dependencies are resolved automatically.

```bash
# Infrastructure + platform + inference (via dependency auto-pull)
./es_auto_installer.sh install inference --env local

# A single layer
./es_auto_installer.sh install platform --env local
./es_auto_installer.sh install inference --env local

# A single component (with auto-pulled dependencies)
./es_auto_installer.sh install kserve --env local

# A single component, skipping dependencies
./es_auto_installer.sh install metallb --only --env local

# Opt-in Intel AI for Enterprise RAG layer (requires init erag first)
./es_auto_installer.sh install erag --env local

# Override a config value at runtime (no file edit needed)
./es_auto_installer.sh install kserve --env local -- -e kserve_version=0.15.0

# Dry run — show what would happen without making changes
./es_auto_installer.sh install inference --env local -- --check

# Pass additional Ansible flags (use -- to separate)
./es_auto_installer.sh install inference --env local -- -vvv
```

**Targets for `install` / `teardown`:**

| Target | What it covers |
|---|---|
| `infrastructure` | kubernetes, storage |
| `platform` | cert_manager, istio, metallb, envoy_gateway, postgresql, keycloak, object_store, seaweedfs, observability |
| `inference` | keycloak_config, envoy_ai_gateway, kserve, litellm, langfuse, llm_services, nri_cpu_balloons |
| `erag` | app_inference_models, app_pre_install, app_vector_databases, app_keycloak_config, app_apisix, app_chat_history, app_nats, app_fingerprint, app_hpa, app_pipeline, app_edp, app_mcp_gateway, app_ui, app_watcher, app_post_install (opt-in, from ext repo) |
| `<component>` | Any individual component name (e.g. `kserve`, `grafana`, `metallb`) |

---

### `teardown`

Remove components in reverse dependency order. Configuration files and environment state are preserved.

```bash
# Remove everything (cluster included)
./es_auto_installer.sh teardown infrastructure --env local

# Remove a single component
./es_auto_installer.sh teardown keycloak --env local

# Remove the Intel AI for Enterprise RAG layer (keeps platform and inference)
./es_auto_installer.sh teardown erag --env local

# Remove a layer but skip dependencies (e.g., tear down cluster without uninstalling erag first)
./es_auto_installer.sh teardown infrastructure --skip erag --env local
```

---

### `validate`

Run post-install health checks.

```bash
./es_auto_installer.sh validate inference --env local
./es_auto_installer.sh validate kserve --env local
./es_auto_installer.sh validate erag --env local
```

Checks are implemented per-component as `tasks/validate.yaml` (asserts, connectivity, replica counts).

---

### `status`

Print a component status table.

```bash
./es_auto_installer.sh status --env local
```

---

### `show`

List all available layers and components, including which are opt-in.

```bash
./es_auto_installer.sh show
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
| See the full command dispatch flow this CLI triggers | [Architecture & Design Document](../reference/architecture.md) |
