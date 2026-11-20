# Getting Started with Intel® Chatbot Toolkit

[← Docs Index](../README.md)

Deploy the full RAG application stack on a single localhost machine — Kubernetes, platform services, inference models, and the RAG application layer — with one installer.

---

## Prerequisites

- Ubuntu 22.04 or 24.04, with the rest of [Prerequisites](prerequisites.md) met
- `sudo` rights. Passwordless is easiest; otherwise the installer has Ansible ask for your password at the start of each run. For remote nodes, set `ansible_become_password` in the inventory instead.
- Behind a proxy: the proxy settings in `env/<name>/global_config.yaml` (the file `init` creates)

---

## Quick Start

```bash
git clone https://github.com/intel/toolkit-manager.git
cd toolkit-manager

./toolkit_manager.sh configure            # 1. one-time machine prep; answer y
./toolkit_manager.sh init erag            # 2. create env/local/ for erag and its dependencies
vim env/local/global_config.yaml          # 3. optional: base_domain_name, proxy

./toolkit_manager.sh install erag         # 4. install everything up to erag
```

1. `configure` installs Python 3.11+, `yq`, `kubectl` and `helm` with `sudo`, after you confirm.
2. `init erag` clones the inference and Chatbot Toolkit repositories into `ext/` and creates the settings files under `env/local/`.
3. The defaults work as they are; the domain is `inteltoolkits.ai` unless you change it.
4. `install erag` builds the cluster and installs infrastructure, platform, inference and erag. It ends with an `INSTALLATION COMPLETE` banner.

> [!Note]
> `--env local` is the default — if your environment is named `local` you can omit it from all commands.
> `init erag` clones every repo in `configs/repos/repos.erag.yaml` (inference first, then erag) and seeds a `config.<layer>.yaml` per layer.
> `install erag` auto-pulls its dependencies (infrastructure → platform → inference); `--only` runs the target alone.
> Component resolving works with traversal, so you can also target individual components directly: `./toolkit_manager.sh install app_pipeline`.

After install, point `kubectl` at the cluster and print the line to add to `/etc/hosts` on each machine that opens the UI:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
DOMAIN=$(yq '.base_domain_name' env/local/global_config.yaml)
echo "$GATEWAY_IP  $DOMAIN keycloak.$DOMAIN s3.$DOMAIN seaweedfs.$DOMAIN grafana.$DOMAIN"
```

Open `https://<base_domain_name>` in a browser (import `env/local/logs/ai-solutions-ca.crt` to drop the certificate warning; see [Access the services](quickstart.md#access-the-services)). Get the admin password:

```bash
kubectl get secret -n keycloak erag-credentials \
  -o jsonpath='{.data.KEYCLOAK_ERAG_ADMIN_PASSWORD}' | base64 -d; echo
```

Log in with **erag-admin** and that password. The same credentials, plus the `erag-user` and `erag-maintainer` accounts, are in `env/local/logs/rag/default_credentials.txt` (readable only by you).

---

## Teardown

```bash
# Remove erag layer only (keeps cluster, platform and inference intact)
./toolkit_manager.sh teardown erag

# Remove the cluster (and everything on it)
./toolkit_manager.sh teardown infrastructure
```

---

## User Guide

### What Gets Installed

The installer deploys in layers, each building on the previous:

| Layer | Components | Purpose |
|-------|-----------|---------|
| **infrastructure** | Kubernetes (Kubespray), storage (local-path / NFS / Ceph) | Cluster foundation |
| **platform** | Node Feature Discovery, NRI CPU balloons, cert-manager, Istio, MetalLB, metrics-server, Envoy Gateway, PostgreSQL, Keycloak, object store (SeaweedFS), Observability | Shared services |
| **inference** | KServe, Envoy AI Gateway, LLM services (model serving) | Model serving |
| **erag** | models, APISIX, vector DBs, NATS, fingerprint, HPA, pipelines, EDP (document ingestion), MCP gateway, chat history, UI | RAG application |

The **erag** layer is opt-in — it lives in `ext/enterprise.ai-erag/deployment/components.yaml` and only appears in the
registry once the env was initialized with `init erag`.

### Layer-Level Install and Teardown

You don't have to deploy everything at once. Target individual layers:

```bash
# Install up to inference (skip erag) — deps are pulled in automatically
./toolkit_manager.sh install inference

# Add erag later
./toolkit_manager.sh install erag

# Tear down only erag
./toolkit_manager.sh teardown erag

# Tear down inference (removes models, KServe) but keep platform
./toolkit_manager.sh teardown inference
```

Node Feature Discovery belongs to the platform layer (`node_feature_discovery`), so
`teardown inference` keeps NFD and its node labels. `teardown platform` removes them,
and on rack-scale clusters with NVIDIA GPUs the GPU operator as well. On OpenShift NFD
comes from its operator, and teardown leaves it.

You can also target individual components directly — component resolving traverses the dependency tree automatically:

```bash
./toolkit_manager.sh install app_pipeline
```

### Listing Available Components and Current State

```bash
./toolkit_manager.sh show      # all layers and their components, including opt-in ones
./toolkit_manager.sh status    # namespaces, pods, helm releases, endpoints of what is deployed
```

### Pipeline Flavours (`init --flavour`)

A flavour is the preset `init` seeds `config.<layer>.yaml` from. For `erag` they are the
directories under `ext/enterprise.ai-erag/deployment/pipelines/` — `chatqna` (the default),
`chatqna_intel_gpu`, `docsum`, `translation`, `audioqna`, `pl_chatqna`:

```bash
./toolkit_manager.sh init erag --flavour docsum
```

Passing an unknown name lists what is available. `--flavour` applies to `init` only, and only
to layers that declare a `config_dir` in `configs/repos/repos.<layer>.yaml` — `init inference
--flavour x` is an error. Switching flavour on an existing env is refused rather than
overwriting your edits: use a fresh `--env`, or delete `env/<name>/config.erag.yaml` to reseed.

### Multiple Environments

The installer supports multiple named environments side by side. The `local` environment is used by default when `--env` is omitted.

```bash
./toolkit_manager.sh init erag --env dev
./toolkit_manager.sh init erag --env staging

# Each has independent config under env/<name>/
vim env/dev/global_config.yaml
vim env/staging/global_config.yaml

# Deploy into a specific env (--env required for non-local envs)
./toolkit_manager.sh install erag --env dev
./toolkit_manager.sh install erag --env staging
```

All envs share one `ext/` checkout, so they cannot be pinned to different repo revisions —
`init` warns when another env wants a different rev. `init erag --upgrade` moves `ext/` onto
the revs the manifests pin now (refuses on local changes, never rewrites your configs).

### BYO Kubernetes Cluster

If you already have a cluster, skip infrastructure provisioning:

```bash
# In global_config.yaml, set:
existing_kubernetes: "/path/to/your/kubeconfig"
```

The installer skips Kubespray entirely and deploys platform + inference + erag on your existing cluster.

### Multi-Node / Remote Deployment

For remote nodes, list them in `env/<name>/nodes.yaml`; the installer generates `inventory/hosts.yaml` from it. See [Multi-Node & BYO Cluster](../deploy/topologies.md) for details.

### Passing Extra Ansible Variables

Pass extra variables or increase verbosity via `--`:

```bash
./toolkit_manager.sh install erag -- -vvv -e edp_enabled=false
```

Or set `TKM_LOG_LEVEL=debug` (verbose logs + ansible `-vvv`) / `trace` (+ bash xtrace).

### Validation

```bash
./toolkit_manager.sh validate erag
```

Runs validation playbooks against deployed components to verify health.

---

## Configuration

Configuration lives under `env/<name>/` and follows a merge-precedence chain (last wins):

1. role defaults in `roles/<name>/defaults/main.yaml` (baseline)
2. `env/<name>/config.<layer>.yaml` — one per layer, dependencies first so a layer's own config wins over its deps'
3. `env/<name>/global_config.yaml` — top-level settings
4. `env/<name>/nodes.yaml` — node/topology settings
5. `-e key=value` after `--` on the command line (highest priority)

### What Lives in env/

The `env/<name>/` directory is the single source of truth for an environment. Everything useful is here:

| File | Purpose |
|------|---------|
| `global_config.yaml` | Domain, TLS, proxy, component toggles |
| `config.erag.yaml` | RAG-specific: registry, models, feature flags, pipeline config (seeded from a flavour) |
| `config.inference.yaml` | Inference: KServe, CPU policy, model serving, API auth (`auth_type`), LiteLLM/Langfuse |
| `models.yaml` | Model catalog, seeded from `ext/enterprise.ai-inference/model_manager/models.yaml` |
| `nodes.yaml` | Node definitions for remote deployments |
| `inventory/hosts.yaml` | Kubespray-compatible inventory (auto-generated or manual) |
| `kubeconfig.yaml` | Cluster kubeconfig written after k8s install — point `KUBECONFIG` here |
| `logs/` | Per-run installer and Ansible logs (at `env/<name>/logs/`) |
| `.solutions.yaml` | Generated by `init` — which layers this env was provisioned for, and at which rev |

### Changing Models

Edit `env/local/config.erag.yaml`:

```yaml
inference_namespace: "llm-inference"
inference_models:
  - name: llama3-8b-awq
    role: llm
  - name: nomic-embed
    role: embedding
  - name: bge-reranker-v2-m3
    role: reranking
```

Each `name` must match an entry in `env/local/models.yaml` (copied from the inference repo's `model_manager/models.yaml`); `role` is `llm`, `embedding` or `reranking`. Re-run `./toolkit_manager.sh install erag` to apply the change.

### Pipeline Configuration

Seeded by the flavour (see [Pipeline Flavours](#pipeline-flavours-init---flavour)), then editable
in `env/local/config.erag.yaml`:

```yaml
pipeline_type: "chatqna"     # also the pipeline namespace unless pipeline_namespace is set
pipeline_variant: "base"     # "base" (pipelines/<type>/pipeline.yaml) or a stem under pipelines/<type>/variants/
```

For `chatqna` the variants are `hybrid-retrieval`, `output_guard`, `query-rewrite`, `retrieve-rerank` and `upload`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Pods stuck in `Init` waiting for models | Models not ready — check `kubectl get llminferenceservice -n llm-inference` |
| `ImagePullBackOff` from `localhost:5000` (only when you build images locally; the default `registry` is `docker.io/intel`) | Registry down or untrusted — check `systemctl status local-registry.service` and that `/etc/containerd/certs.d/<host>:5000/hosts.toml` exists; on multi-node push with `deployment/update_images.sh --registry <node-ip>:5000`, not `localhost` |
| MetalLB no external IP | Leave `metallb_ip_range` unset so it is auto-detected, or set a free range in `global_config.yaml` |
| Keycloak unreachable | Confirm `/etc/hosts` has `keycloak.<base_domain_name>` pointing to gateway IP |
| Installer fails at "yq missing" | Run `./toolkit_manager.sh configure` first |
| Installer prompts for sudo password | Add `NOPASSWD` to sudoers to avoid repeated prompts |

### Useful Commands

```bash
# Check cluster health
kubectl get nodes
kubectl get pods -A --field-selector=status.phase!=Running

# Check RAG pipeline pods (namespace == pipeline_type)
kubectl get pods -n chatqna
kubectl get pods -n edp
kubectl get pods -n vdb

# Check inference model readiness
kubectl get llminferenceservice -n llm-inference

# View installer/ansible logs (stored per-environment)
ls env/local/logs/
```

### Working with kubectl and k9s

After install, the cluster kubeconfig is at `env/local/kubeconfig.yaml`. Export it once in your shell:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
```

For a richer interactive view, use [k9s](https://k9scli.io/) — it picks up `KUBECONFIG` automatically:

```bash
k9s
```

Browse pods, logs, exec into containers, and filter by namespace — all from the terminal.

---

## Next Steps

| Goal | Guide |
|------|-------|
| Model management | [Deploy Models](../deploy/deploy_models.md) |
| Architecture overview | [Architecture](../reference/architecture.md) |
| Network details | [Network Architecture](../deploy/networking.md) |
| Adding your own solution layer | [Adding Solutions](../reference/adding_solutions.md) |
| Platform getting started | [Getting Started](quickstart.md) |
