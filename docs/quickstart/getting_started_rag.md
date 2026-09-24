# Getting Started with Intel® AI for Enterprise RAG

[← Docs Index](../README.md)

Deploy the full RAG application stack on a single localhost machine — Kubernetes, platform services, inference models, and the RAG application layer — with one installer.

---

## Prerequisites

- Ubuntu 22.04 or 24.04
- Sudo access (passwordless is most convenient; the installer will prompt for a password if needed for localhost deployment, for remote easiest is to use ansible_become_password via inventory)
- Proxy configured in `env/<name>/global_config.yaml` (written by `init`) if behind one

---

## Quick Start

```bash
git clone https://github.com/intel/enterprise-ai-solutions.git
cd enterprise-ai-solutions

./es_auto_installer.sh configure
./es_auto_installer.sh init erag
vim env/local/global_config.yaml          # set base_domain_name, proxy if needed

./es_auto_installer.sh install erag
```

> [!Note]
> `--env local` is the default — if your environment is named `local` you can omit it from all commands.
> `init erag` clones every repo in `configs/repos/repos.erag.yaml` (inference first, then erag) and seeds a `config.<layer>.yaml` per layer.
> `install erag` auto-pulls its dependencies (infrastructure → platform → inference); `--only` runs the target alone.
> Component resolving works with traversal, so you can also target individual components directly: `./es_auto_installer.sh install app_pipeline`.

After install, add the gateway IP to `/etc/hosts`:

```bash
GATEWAY_IP=$(kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}')
DOMAIN=$(yq '.base_domain_name' env/local/global_config.yaml)
echo "$GATEWAY_IP  $DOMAIN keycloak.$DOMAIN s3.$DOMAIN grafana.$DOMAIN"
```

Open `https://<base_domain_name>` in a browser. Retrieve credentials:

```bash
kubectl get secret -n keycloak erag-credentials \
  -o jsonpath='{.data.KEYCLOAK_ERAG_ADMIN_PASSWORD}' | base64 -d
```

Log in with **erag-admin** and the decoded password.

---

## Teardown

```bash
# Remove erag layer only (keeps cluster, platform and inference intact)
./es_auto_installer.sh teardown erag

# Remove the cluster (and everything on it)
./es_auto_installer.sh teardown infrastructure
```

---

## User Guide

### What Gets Installed

The installer deploys in layers, each building on the previous:

| Layer | Components | Purpose |
|-------|-----------|---------|
| **infrastructure** | Kubernetes (Kubespray), storage (local-path / NFS / Ceph) | Cluster foundation |
| **platform** | cert-manager, Istio, MetalLB, Envoy Gateway, PostgreSQL, Keycloak, object store (MinIO), Observability | Shared services |
| **inference** | KServe, Envoy AI Gateway, LLM services, NRI CPU balloons | Model serving |
| **erag** | models, APISIX, vector DBs, NATS, fingerprint, HPA, pipelines, EDP, chat history, UI | RAG application |

The **erag** layer is opt-in — it lives in `ext/enterprise.ai-erag/deployment/components.yaml` and only appears in the
registry once the env was initialized with `init erag`.

### Layer-Level Install and Teardown

You don't have to deploy everything at once. Target individual layers:

```bash
# Install up to inference (skip erag) — deps are pulled in automatically
./es_auto_installer.sh install inference

# Add erag later
./es_auto_installer.sh install erag

# Tear down only erag
./es_auto_installer.sh teardown erag

# Tear down inference (removes models, KServe) but keep platform
./es_auto_installer.sh teardown inference
```

You can also target individual components directly — component resolving traverses the dependency tree automatically:

```bash
./es_auto_installer.sh install app_pipeline
```

### Listing Available Components and Current State

```bash
./es_auto_installer.sh show      # all layers and their components, including opt-in ones
./es_auto_installer.sh status    # namespaces, pods, helm releases, endpoints of what is deployed
```

### Pipeline Flavours (`init --flavour`)

A flavour is the preset `init` seeds `config.<layer>.yaml` from. For `erag` they are the
directories under `ext/enterprise.ai-erag/deployment/pipelines/` — `chatqna` (the default),
`docsum`, `translation`, `audioqna`, `pl_chatqna`:

```bash
./es_auto_installer.sh init erag --flavour docsum
```

Passing an unknown name lists what is available. `--flavour` applies to `init` only, and only
to layers that declare a `config_dir` in `configs/repos/repos.<layer>.yaml` — `init inference
--flavour x` is an error. Switching flavour on an existing env is refused rather than
overwriting your edits: use a fresh `--env`, or delete `env/<name>/config.erag.yaml` to reseed.

### Multiple Environments

The installer supports multiple named environments side by side. The `local` environment is used by default when `--env` is omitted.

```bash
./es_auto_installer.sh init erag --env dev
./es_auto_installer.sh init erag --env staging

# Each has independent config under env/<name>/
vim env/dev/global_config.yaml
vim env/staging/global_config.yaml

# Deploy into a specific env (--env required for non-local envs)
./es_auto_installer.sh install erag --env dev
./es_auto_installer.sh install erag --env staging
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

For remote nodes edit `env/<name>/inventory/hosts.yaml` directly in kubespray format. See [Multi-Node & BYO Cluster](../deploy/topologies.md) for details.

### Passing Extra Ansible Variables

Pass extra variables or increase verbosity via `--`:

```bash
./es_auto_installer.sh install erag -- -vvv -e edp_enabled=false
```

Or set `ES_LOG_LEVEL=debug` (verbose logs + ansible `-vvv`) / `trace` (+ bash xtrace).

### Validation

```bash
./es_auto_installer.sh validate erag
```

Runs validation playbooks against deployed components to verify health.

---

## Configuration

Configuration lives under `env/<name>/` and follows a merge-precedence chain (last wins):

1. role defaults in `roles/<name>/defaults/main.yaml` (baseline)
2. `env/<name>/config.<layer>.yaml` — one per layer, dependencies first so a layer's own config wins over its deps'
3. `env/<name>/global_config.yaml` — top-level settings
4. `env/<name>/nodes.yaml` — node/topology settings (highest priority)

### What Lives in env/

The `env/<name>/` directory is the single source of truth for an environment. Everything useful is here:

| File | Purpose |
|------|---------|
| `global_config.yaml` | Domain, TLS, auth provider, proxy, component toggles |
| `config.erag.yaml` | RAG-specific: registry, models, feature flags, pipeline config (seeded from a flavour) |
| `config.inference.yaml` | Inference: KServe, CPU policy, model serving |
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
  - name: bge-base-en
    role: embedding
  - name: bge-reranker
    role: reranking
```

Model names must match entries in `env/local/models.yaml` (seeded from the inference repo's `model_manager/models.yaml`). Currently only defaults are enabled.

### Pipeline Configuration

Seeded by the flavour (see [Pipeline Flavours](#pipeline-flavours-init---flavour)), then editable
in `env/local/config.erag.yaml`:

```yaml
pipeline_type: "chatqna"     # also the pipeline namespace unless pipeline_namespace is set
pipeline_variant: "base"     # "base" (pipelines/<type>/pipeline.yaml) or a stem under pipelines/<type>/variants/
upload_pipelines: false      # enable document upload pipelines
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Pods stuck in `Init` waiting for models | Models not ready — check `kubectl get llminferenceservice -n llm-inference` |
| `ImagePullBackOff` from `localhost:5000` | Registry down or untrusted — check `systemctl status local-registry.service` and that `/etc/containerd/certs.d/<host>:5000/hosts.toml` exists; on multi-node use `--registry <node-ip>:5000`, not `localhost` |
| MetalLB no external IP | Verify `metallb_ip_range` in `nodes.yaml` or let auto-detect handle it |
| Keycloak unreachable | Confirm `/etc/hosts` has `keycloak.<base_domain_name>` pointing to gateway IP |
| Installer fails at "yq missing" | Run `./es_auto_installer.sh configure` first |
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
