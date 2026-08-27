# Meet Intel® AI for Enterprise Solutions

[← Docs Index](../README.md)

> **One automated workflow connecting Intel's enterprise AI portfolio — self-hosted, on-premises, and ready to deploy.**

Intel® AI for Enterprise Solutions is an open source, Kubernetes-based platform that removes the complexity of deploying self-hosted enterprise AI on Intel® Xeon® processors — on-premises, air-gapped, or in your own private cloud, with no GPU required and no data leaving your infrastructure.

The repository acts as the integration and automation layer across Intel's enterprise AI portfolio. It connects the infrastructure, inference engines, models, retrieval services, security, and observability required to deploy complete AI solutions — through a consistent, automated workflow.

Instead of requiring teams to manually assemble and configure each component, the platform takes them from bare metal or an existing Kubernetes cluster to a secured, observable, OpenAI-compatible AI environment. Because inference could run entirely locally — a meaningful advantage for regulated or data-sovereignty-sensitive environments that need to keep inference in-house.

The platform brings together:

- Automated infrastructure deployment and lifecycle management via Ansible + Kubespray
- Model serving through vLLM and OpenVINO™ Model Server
- OpenAI-compatible endpoints (drop-in for any OpenAI SDK client)
- TLS, authentication, and access control (Keycloak OIDC or LiteLLM virtual keys)
- AI-aware inference routing via Envoy AI Gateway
- Persistent model storage (NFS, local-path, Ceph)
- NUMA-aware CPU optimization via NRI CPU Balloons
- Retrieval-augmented generation services (eRAG — opt-in)
- Metrics, logs, traces, and LLM observability (Prometheus, Grafana, Loki, Tempo, Langfuse)

---

## Why Choose Intel® AI for Enterprise Solutions Instead of Building It Yourself?

Enterprise AI requires more than a model server.

Teams also need Kubernetes, networking, storage, TLS, identity, model routing, observability, and workload placement. Building and integrating those layers individually can take significant engineering effort — weeks to months.

AI for Enterprise Solutions deploys them through one environment-based workflow:

```bash
./es_auto_installer.sh install --all --env <name>
```

The installer deploys enabled layers in dependency order:

```
Infrastructure  (Kubernetes, storage)
      ↓
Platform services  (cert-manager, Istio, MetalLB, Envoy Gateway, PostgreSQL, Keycloak, MinIO, Observability)
      ↓
Inference  (KServe, Envoy AI Gateway, LiteLLM, Langfuse, model runtimes)
      ↓
Applications  (RAG pipelines, vector DBs, chat UI — opt-in)
```

---

## What Makes Intel® AI for Enterprise Solutions Different?

### One entry point

`es_auto_installer.sh` manages environment initialization, installation, validation, status, and teardown. There is no separate toolchain to learn for each layer.

### OpenAI-compatible API

Applications and frameworks connect through a familiar API — the same `base_url` + `api_key` pattern used by the OpenAI SDK, LangChain, LlamaIndex, Cursor, n8n, and thousands of other tools.

### GPU-Free Inference on Intel® Silicon

The platform is designed for AI inference on Intel® silicon, with NUMA-aware CPU placement (NRI Balloons) and AMX instruction acceleration on Intel® Xeon® — delivering high-throughput, GPU-free inference without a specialized accelerator supply chain.

### Modular by design

Every platform component can be enabled, disabled, or replaced without rebuilding the complete architecture. Bring your own cert-manager, load balancer, identity provider, or service mesh — disable the built-in one with a single config flag.

### Multi-environment from one bastion

Each named environment under `env/<name>/` is fully isolated — its own config, inventory, kubeconfig, and model catalog. Run dev, staging, and prod from one machine.

### Built-in operations

Prometheus, Grafana, Loki, Tempo, OpenTelemetry, and Langfuse provide infrastructure and AI-workload visibility. Grafana dashboards are pre-wired; no manual datasource setup required.

---

## Solution layers

| Layer | What it provides | Included in `--all` |
|---|---|---|
| **Inference** | Model serving, routing, authentication, OpenAI-compatible endpoints | Yes |
| **RAG (eRAG)** | Document ingestion, vector search, grounded chat, chat history, UI | No — opt-in via `install application` |
| **Agentic AI** | Agent orchestration, tool calling, sandboxed execution, multi-agent workflows | Planned |

Each layer builds on the one below it. Deploy what you need today and add layers as your use case evolves.

---

## Platform components

| # | Component | Purpose |
|---|---|---|
| 1 | **Kubernetes** | Container orchestration — foundation for all workloads |
| 2 | **Storage** | Shared persistent volumes for model weights (local-path / NFS / Ceph) |
| 3 | **Cert-Manager** | Automated TLS certificate issuance and rotation |
| 4 | **Istio (Ambient)** | Zero-sidecar service mesh — mTLS, traffic policies, no pod restarts |
| 5 | **MetalLB** | Bare-metal load balancer — assigns external IPs from real node addresses |
| 6 | **Envoy Gateway** | Kubernetes Gateway API — HTTPS ingress, TLS termination, JWT auth |
| 7 | **PostgreSQL (CNPG)** | Managed database for Keycloak, LiteLLM, Langfuse |
| 8 | **Keycloak** | OIDC/OAuth2 identity provider — SSO, RBAC, API keys |
| 9 | **MinIO** | S3-compatible object store for model weights and log storage |
| 10 | **Observability** | Prometheus + Grafana + Loki + Tempo + OpenTelemetry + Prometheus Adapter |
| 11 | **Envoy AI Gateway** | AI-aware routing — token-based load balancing, model-level rate limiting |
| 12 | **KServe** | Model serving platform — InferenceService & LLMInferenceService CRDs |
| 13 | **LiteLLM** | OpenAI-compatible proxy — unified endpoint, virtual key management |
| 14 | **Valkey** | In-memory cache (Redis-compatible) for LiteLLM and Langfuse |
| 15 | **Langfuse** | LLM observability — traces, token usage, cost tracking, prompt management |
| 16 | **NRI CPU Balloons** | NUMA-aware CPU pinning for vLLM — eliminates noisy-neighbor interference |

---

## Deployment options

| Option | How | Best for |
|---|---|---|
| **Single-node** | Installs Kubernetes on localhost | Development, evaluation, compact environments |
| **Multi-node** | Provisions Kubernetes across remote nodes via SSH | Production, scale-out, higher availability |
| **Existing Kubernetes** | Skips Kubespray; deploys onto your cluster | Managed clusters, brownfield environments |

---

## Start here

| Goal | Link |
|---|---|
| Run the platform in 3 steps | [Quick Start](../quickstart/quickstart.md) |
| All deployment options (multi-node, BYO, config) | [Getting Started](../quickstart/quickstart.md) |
| Deploy models | [Deploy a Model](../deploy/deploy_models.md) |
| Multi-node or BYO cluster | [Multi-Node & BYO Cluster](../deploy/topologies.md) |
| Connect your app or framework | [Integration Guide](../customize/integration.md) |
| Architecture deep-dive | [Architecture](../reference/architecture.md) |
| Configuration reference | [Configuration](../customize/configuration.md) |
