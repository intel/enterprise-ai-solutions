# Meet Toolkit Manager

[← Docs Index](../README.md)

> **One automated workflow connecting Intel's AI portfolio — self-hosted, on-premises, and ready to deploy.**

Toolkit Manager is an open source, Kubernetes-based platform that removes the complexity of deploying self-hosted AI on Intel® Xeon® processors — on-premises, air-gapped, or in your own private cloud, with no GPU required and no data leaving your infrastructure.

The repository acts as the integration and automation layer across Intel's AI portfolio. It connects the infrastructure, inference engines, models, retrieval services, security, and observability required to deploy complete AI solutions — through a consistent, automated workflow.

Instead of requiring teams to assemble and configure each component by hand, the platform takes them from bare metal or an existing Kubernetes cluster to a secured, observable, OpenAI-compatible AI environment. Inference can run entirely on your own machines, which matters for regulated or data-sovereignty-sensitive environments that must keep it in-house.

The platform brings together:

- Automated infrastructure deployment and lifecycle management via Ansible + Kubespray
- Model serving through vLLM and OpenVINO™ Model Server
- OpenAI-compatible endpoints (drop-in for any OpenAI SDK client)
- TLS, authentication, and access control (Keycloak sign-in and tokens, or LiteLLM virtual keys)
- AI-aware inference routing via Envoy AI Gateway (or agentgateway)
- Persistent model storage (local-path, NFS, Ceph, NetApp ONTAP, or your own CSI driver)
- NUMA-aware CPU optimization via NRI CPU Balloons
- Retrieval-augmented generation (RAG) services (Intel® Chatbot Toolkit — opt-in) and agent services (Intel® Agent Toolkit — opt-in)
- Metrics, logs, traces, and LLM observability (Prometheus, Grafana, Loki, Tempo, Langfuse)

---

## Why Choose Toolkit Manager Instead of Building It Yourself?

A production deployment requires more than a model server.

Teams also need Kubernetes, networking, storage, TLS, identity, model routing, observability, and workload placement. Building and integrating those layers individually can take significant engineering effort — weeks to months.

Toolkit Manager deploys them through one environment-based workflow:

```bash
./toolkit_manager.sh install inference --env <name>
```

The installer deploys enabled layers in dependency order:

```
Infrastructure  (Kubernetes, storage)
      ↓
Platform services  (cert-manager, Istio, MetalLB, Envoy Gateway, PostgreSQL, Keycloak, SeaweedFS, Observability)
      ↓
Inference  (KServe, Envoy AI Gateway, model runtimes; LiteLLM and Langfuse when enabled)
      ↓
Solutions  (Intel® Chatbot Toolkit or Intel® Agent Toolkit — opt-in)
```

---

## What Makes Toolkit Manager Different?

### One entry point

`toolkit_manager.sh` manages environment initialization, installation, validation, status, node scaling, and teardown. There is no separate toolchain to learn for each layer.

### OpenAI-compatible API

Applications and frameworks connect through a familiar API — the same `base_url` + `api_key` pattern used by the OpenAI SDK, LangChain, LlamaIndex, Cursor, n8n, and thousands of other tools.

### GPU-Free Inference on Intel® Xeon®

The platform is designed for AI inference on Intel® Xeon® processors, with NUMA-aware CPU placement (NRI Balloons) and AMX instruction acceleration — delivering high-throughput, GPU-free inference without a specialized accelerator supply chain.

### Modular by design

Optional components — Istio, observability, Headlamp, KEDA, Velero and others — are switched on or off with one setting each in `global_config.yaml`. On a cluster you already run, `cert_manager_enabled: false` and `metallb_enabled: false` keep your own cert-manager and load balancer. Keycloak and the gateway are always installed.

### Multi-environment from one bastion

Each named environment under `env/<name>/` has its own config, inventory, kubeconfig, and model catalog. Run dev, staging, and prod from one machine. The downloaded toolkit repositories in `ext/` are shared by all environments, so they are always at one revision.

### Built-in operations

Prometheus, Grafana, Loki, Tempo and OpenTelemetry provide infrastructure and AI-workload visibility, and Langfuse traces LLM calls when LiteLLM is on. Grafana dashboards and data sources come pre-configured.

---

## Solution layers

| Layer | What it provides | How to deploy |
|---|---|---|
| **Intel® Inference Toolkit** | Model serving, routing, authentication, OpenAI-compatible endpoints | `init inference` + `install inference` (pulls infrastructure + platform automatically) |
| **Intel® Chatbot Toolkit** | Document ingestion, vector search, grounded chat, chat history, UI | `init erag` + `install erag` (pulls infrastructure + platform + inference) |
| **Intel® Agent Toolkit** | Agent memory (Redis Stack, pgvector), sandboxed code execution (Agent Sandbox, optionally in Kata microVMs, and OpenShell), an agent realm in Keycloak, optional KubeRay | `init agent-toolkit` + `install agent-toolkit` (pulls infrastructure + platform + inference) |

Each layer builds on the one below it. Deploy what you need today and add layers as your use case evolves. **Intel® Chatbot Toolkit and Intel® Agent Toolkit are opt-in** — installing `inference` brings neither; you must initialize and install them explicitly.

### Which repository do I start from?

This one. Several repositories make up the portfolio, but you only ever clone and work from this one; `init` fetches the others into `ext/` at the revisions this repo pins.

```
github.com/intel/toolkit-manager            <- start here; run all commands from this root
  |
  |-- toolkit_manager.sh        the single entry point (configure / init / install / teardown)
  |-- model-manager             deploy and manage models
  |-- env/<name>/               your configuration, inventory, kubeconfig, and logs
  `-- ext/                      cloned automatically by `init`
        |-- enterprise.ai-inference    Intel® Inference Toolkit: KServe, vLLM, OpenVINO Model Server
        |-- enterprise.ai-erag         Intel® Chatbot Toolkit: pipelines, ingestion, UI
        `-- enterprise.agent-toolkit   Intel® Agent Toolkit: agent memory, sandboxes
```

> [!IMPORTANT]
> Do not clone the Intel® Inference Toolkit, Intel® Chatbot Toolkit or Intel® Agent Toolkit repositories yourself to deploy them. They are pulled in as pinned dependencies, and every command in this documentation runs from the root of this repository.

---

## Platform components

| # | Component | Purpose |
|---|---|---|
| 1 | **Kubernetes** | Container orchestration — foundation for all workloads |
| 2 | **Storage** | Persistent volumes for model weights and databases (local-path / NFS / Ceph / NetApp ONTAP) |
| 3 | **Cert-Manager** | Automated TLS certificate issuance and rotation |
| 4 | **Istio (Ambient)** | Zero-sidecar service mesh — mTLS, traffic policies, no pod restarts |
| 5 | **MetalLB** | Bare-metal load balancer — assigns external IPs from real node addresses |
| 6 | **Envoy Gateway** | Kubernetes Gateway API — HTTPS ingress, TLS termination, JWT auth |
| 7 | **PostgreSQL (CNPG)** | Managed database for Keycloak, LiteLLM, Langfuse |
| 8 | **Keycloak** | OIDC/OAuth2 identity provider — single sign-on, roles, and the tokens API clients send |
| 9 | **SeaweedFS** | S3-compatible object store for logs (Loki), traces (Tempo) and Langfuse |
| 10 | **Observability** | Prometheus + Grafana + Loki + Tempo + OpenTelemetry + Prometheus Adapter |
| 11 | **Envoy AI Gateway** | AI-aware routing — token-based load balancing, model-level rate limiting |
| 12 | **KServe** | Model serving platform — InferenceService & LLMInferenceService CRDs |
| 13 | **LiteLLM** | OpenAI-compatible proxy — virtual keys with budgets (with `auth_type: litellm_vkey`, or `litellm_enabled: true`) |
| 14 | **Valkey** | In-memory cache (Redis-compatible) for LiteLLM and Langfuse, installed with them |
| 15 | **Langfuse** | LLM observability — traces, token usage, cost tracking, prompt management; installed with LiteLLM |
| 16 | **NRI CPU Balloons** | NUMA-aware CPU pinning for model servers — keeps other workloads off their cores |

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
| Run the platform in 3 steps | [Quick Start](../../README.md#quick-start) |
| All deployment options (multi-node, BYO, config) | [Getting Started](../quickstart/quickstart.md) |
| Deploy models | [Deploy a Model](../deploy/deploy_models.md) |
| Multi-node or BYO cluster | [Multi-Node & BYO Cluster](../deploy/topologies.md) |
| Connect your app or framework | [Integration Guide](../customize/integration.md) |
| Architecture deep-dive | [Architecture](../reference/architecture.md) |
| Common questions | [FAQ](../faq.md) |
| What a term means | [Glossary](../glossary.md) |
| Configuration reference | [Configuration](../customize/configuration.md) |
