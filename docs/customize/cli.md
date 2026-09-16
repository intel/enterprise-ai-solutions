# CLI Reference

[← Docs index](../README.md)

`es_auto_installer.sh` is the installer CLI. It creates environments, installs
and tears down layers and components, validates health, and reports status.
`model-manager` is the CLI for the inference toolkit: deploy, undeploy, list,
and scale models.

Run both from the repository root. `--env` selects which `env/<name>/` directory
a command acts on. If you omit it, the installer uses `local`.

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

### `init <name>`

Create a new environment directory at `env/<name>/` and seed it with default configuration files.

```bash
./es_auto_installer.sh init local           # standard environment
./es_auto_installer.sh init prod --rag      # seed an additional toolkit config
```

Creates:
- `env/<name>/global_config.yaml`, edit this before installing
- `env/<name>/nodes.yaml`, node IPs and SSH credentials (edit for multi-node)
- `env/<name>/inventory/hosts.yaml`, targets localhost by default; edit for multi-node
- `env/<name>/models.yaml`, model catalog, pre-seeded from the inference repo defaults

---

### `install`

Deploy components. Dependencies are resolved automatically.

```bash
# Full stack (infrastructure + platform + inference)
./es_auto_installer.sh install --all --env local

# A single layer
./es_auto_installer.sh install platform --env local
./es_auto_installer.sh install inference --env local

# A single component (with auto-pulled dependencies)
./es_auto_installer.sh install kserve --env local

# A single component, skipping dependencies
./es_auto_installer.sh install metallb --only --env local

# Opt-in application layer (other Intel® enterprise AI toolkits)
./es_auto_installer.sh install application --env local

# Override a config value at runtime (no file edit needed)
./es_auto_installer.sh install kserve --env local -- -e kserve_version=0.15.0

# Dry run: show what would happen without making changes
./es_auto_installer.sh install --all --env local -- --check

# Pass additional Ansible flags (use -- to separate)
./es_auto_installer.sh install --all --env local -- -vvv
```

**Targets for `install` / `teardown`:**

| Target | What it covers |
|---|---|
| `--all` | infrastructure + platform + inference |
| `infrastructure` | kubernetes, storage |
| `platform` | cert_manager, istio, metallb, envoy_gateway, postgresql, keycloak, object_store, minio, observability |
| `inference` | keycloak_config, envoy_ai_gateway, kserve, litellm, langfuse, llm_services, nri_cpu_balloons |
| `application` | Other Intel® enterprise AI toolkits (opt-in, from ext repos) |
| `<component>` | Any individual component name (e.g. `kserve`, `grafana`, `metallb`) |

---

### `teardown`

Remove components in reverse dependency order. Configuration files and environment state are preserved.

```bash
# Remove everything
./es_auto_installer.sh teardown --all --env local

# Remove a single component
./es_auto_installer.sh teardown keycloak --env local

# Remove the application layer (keeps platform and inference)
./es_auto_installer.sh teardown application --env local
```

---

### `validate`

Run post-install health checks.

```bash
./es_auto_installer.sh validate --all --env local
./es_auto_installer.sh validate kserve --env local
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
| `--all` | Select the full stack (infra + platform + inference) |
| `--only` | Skip dependency auto-inclusion, run the named target alone |
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
| See the full command dispatch flow this CLI triggers | [Architecture & Design](../meet/architecture.md) |
