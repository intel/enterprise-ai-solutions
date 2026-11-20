# Toolkit Manager

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Platform: Intel Xeon](https://img.shields.io/badge/Platform-Intel%C2%AE%20Xeon%C2%AE-0068B5)](https://www.intel.com/xeon)
[![Deployment: Kubernetes](https://img.shields.io/badge/Deployment-Kubernetes-326CE5)](https://kubernetes.io)
[![Serving: vLLM · OVMS](https://img.shields.io/badge/Serving-vLLM%20%C2%B7%20OVMS-purple)](https://vllm.ai)
[![Gateway: Envoy](https://img.shields.io/badge/Gateway-Envoy%20AI%20Gateway-orange)](https://gateway.envoyproxy.io)
[![API: OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20Compatible-green)](https://platform.openai.com/docs/api-reference)

**The fast path to deploying AI on Intel® Xeon® processors. Go from bare metal to a working AI stack with one installer.**

> Deploy and connect LLM inference, RAG, and agentic workflows in a customizable Kubernetes-based platform — with a GenAI gateway, security, intelligent routing, observability, model serving, and infrastructure automation built in.

---

## What is Toolkit Manager?

A production AI stack needs more than a model endpoint. Teams need to connect LLM inference, RAG, and agentic workflows, along with security, routing, and observability.

Toolkit Manager wires these pieces together in a pre-integrated, customizable Kubernetes stack optimized for Intel® Xeon® processors. One installer, `toolkit_manager.sh`, takes bare-metal machines through Kubernetes provisioning, platform services (networking, security, observability), model serving, and AI gateway setup, and ends with OpenAI-compatible endpoints.

You get a secured, load-balanced stack where you can serve LLMs, run RAG pipelines, and monitor everything through one gateway. Start with the defaults, then configure or extend as needed.

## Architecture

The stack is built in layers. You install one **target**, and the installer adds every layer below it for you.

| # | Layer | What it adds | Install target | Links |
| --- | --- | --- | --- | --- |
| 1 | **Infrastructure** | Kubernetes (via Kubespray) and storage (local-path, NFS, Ceph, NetApp ONTAP, or your own CSI driver) | `infrastructure` | [Quick Start](#quick-start) |
| 2 | **Platform** | cert-manager, Istio ambient mesh, MetalLB, Envoy Gateway, PostgreSQL, Keycloak, SeaweedFS, observability (Grafana, Prometheus, Loki, Tempo) | `platform` | [Quick Start](#quick-start) |
| 3 | **Intel® Inference Toolkit** | Envoy AI Gateway, KServe, vLLM / OpenVINO™ Model Server, optional LiteLLM and Langfuse: the model endpoint that RAG and agents build on | `inference` | [inference-toolkit](https://github.com/intel/inference-toolkit) |
| 4 | **Intel® Chatbot Toolkit** *(optional)* | Vector database, document ingestion (EDP), RAG pipeline orchestration (GMC), MCP gateway, chat history, web UI | `erag` | [chatbot-toolkit](https://github.com/intel/chatbot-toolkit) |
| 4 | **Intel® Agent Toolkit** *(optional)* | Agent Sandbox (optional Kata microVMs), OpenShell, Redis Stack, pgvector, KubeRay, LiteLLM virtual keys, guardrail and hybrid router | `agent-toolkit` | [agent-toolkit](https://github.com/intel/agent-toolkit) |

You never clone the other repositories yourself: `init` downloads the ones your target needs into `ext/`.

**Request flow:** a request enters through Envoy Gateway, which checks its credential (a Keycloak token, or a LiteLLM virtual key). The Envoy AI Gateway then routes it to the server running the requested model. Every layer's health and latency is visible in the built-in Grafana, Prometheus, Loki and Tempo stack.

<p align="center">
  <img src="docs/assets/toolkit-manager-architecture.png" alt="Toolkit Manager architecture: Toolkit Manager is the core repository and one installer (git clone, then the installer, then the solutions). It auto-deploys a platform with inference, RAG, and agents. Four blocks stack upward: Infrastructure (Kubernetes, Intel infrastructure, OS and drivers, bring-your-own cluster) at the base; Platform (gateway, auth, routing, observability, serving, storage) builds on infrastructure; Model Inference (Intel® Inference Toolkit and Intel® Inference Microservices) builds on the platform; Solutions requires inference. Chatbot/RAG (Intel® Chatbot Toolkit) and Agents (Intel® Agent Toolkit) are marked coming soon." />
</p>

---

## Quick Start

One machine, default settings. Allow up to an hour for the first install: most of that time is spent downloading container images.

> [!NOTE]
> **Prerequisites:** Ubuntu 22.04/24.04 (or RHEL/Rocky Linux), `git`, `sudo` rights (passwordless is easiest), and internet access. Full list → [Prerequisites](docs/quickstart/prerequisites.md).

### Step 1 - Install

1. Get the installer and prepare the machine that will become the cluster:

   ```bash
   git clone https://github.com/intel/toolkit-manager.git
   cd toolkit-manager
   ./toolkit_manager.sh configure     # one-time machine prep; answer y to the prompt
   ```

   `configure` uses `sudo` to install Python 3.11+, `yq`, `kubectl` and `helm`, and creates the installer's Python environment in `.venv/`. It asks before changing anything. Skip it if those tools are already installed.

2. Pick how far up the stack to go, and run its two commands. Each target includes every layer below it, so run one pair only:

   ```bash
   # Layer 1: Kubernetes and storage
   ./toolkit_manager.sh init infrastructure && ./toolkit_manager.sh install infrastructure

   # Layers 1-2: + platform services (gateway, Keycloak, observability, ...)
   ./toolkit_manager.sh init platform && ./toolkit_manager.sh install platform

   # Layers 1-3: + model serving with an OpenAI-compatible endpoint
   ./toolkit_manager.sh init inference && ./toolkit_manager.sh install inference

   # Layers 1-4: + Intel® Chatbot Toolkit (RAG application)
   ./toolkit_manager.sh init erag && ./toolkit_manager.sh install erag

   # Layers 1-4: + Intel® Agent Toolkit
   ./toolkit_manager.sh init agent-toolkit && ./toolkit_manager.sh install agent-toolkit
   ```

   - `init` creates the environment folder `env/local/` with your settings files, and downloads the repositories the target needs into `ext/`.
   - `install` builds the Kubernetes cluster and installs every layer up to the target. A successful run ends with an `INSTALLATION COMPLETE` banner that names the log file under `env/local/logs/`.

   To go higher later, run the pair for the higher target on the same environment.

### Step 2 - Check the cluster

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes                  # the node shows STATUS Ready
./toolkit_manager.sh status        # what is installed, and the service URLs
```

Settings live in `env/local/global_config.yaml` and, for the inference layer (including `auth_type`), `env/local/config.inference.yaml`, both created by `init`. The defaults are a single local node, the domain `inteltoolkits.ai`, and Keycloak tokens (`auth_type: jwt`) for API access. Every option is in the [Configuration Reference](docs/customize/configuration.md).

`--env` defaults to `local`. Install and teardown are scoped to an environment: what you installed with `--env prod` must be torn down with `--env prod`. To remove everything (your config files are kept), run `./toolkit_manager.sh teardown infrastructure`.

### Step 3 - Serve a model and call it

This step needs the `inference`, `erag` or `agent-toolkit` target. `model-manager` downloads the weights, picks serving parameters, and creates the pod. No YAML is needed:

```bash
./model-manager deploy qwen3-0-6b --wait
```

The helper script reads the gateway address and Keycloak credentials from the cluster and exports a token (JWT) for the request:

```bash
source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
# Exports: TOKEN, GATEWAY_IP, GATEWAY_DOMAIN, GATEWAY_URL

curl -s --noproxy '*' \
  --cacert env/local/logs/ai-solutions-ca.crt \
  --resolve "$GATEWAY_DOMAIN:443:$GATEWAY_IP" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}' \
  "$GATEWAY_URL/v1/chat/completions"
```

A JSON reply with a `choices` list means the model is serving. `--resolve` sends the request to the gateway IP without an `/etc/hosts` entry, and `--cacert` trusts the installer's self-signed certificate authority. Everything speaks the OpenAI-compatible API, so any client that works with OpenAI works here.

### Next steps

| You want to | Follow |
|---|---|
| Deploy more models: gated or any Hugging Face model, the Python SDK, the LiteLLM (`litellm_vkey`) access flow | [Deploy a Model](docs/deploy/deploy_models.md) |
| Connect your own app, agent, or framework to the endpoint | [Integration Guide](docs/customize/integration.md) |
| Build a RAG application: document ingestion, vector search, chat UI | [Getting Started with Intel® Chatbot Toolkit](docs/quickstart/getting_started_rag.md) |
| Run agents: sandboxes, memory stores, a governed LLM gateway | [Getting Started with the Intel® Agent Toolkit](docs/quickstart/getting_started_agent_toolkit.md) |

---

## Documentation

Everything beyond the Quick Start, grouped by task. The [Documentation Index](docs/README.md) lists every guide.

| Task | Guide |
|---|---|
| **Deploy and operate** | |
| Install step by step: multi-node, bastion host, bring-your-own cluster, auth types | [Deployment Guide](docs/deploy/install_platform.md) |
| Spread the cluster over several machines, or use an existing cluster | [Multi-Node & BYO Cluster](docs/deploy/topologies.md) |
| Build a cluster on a dedicated high-speed fabric | [Rack-scale Deployment](docs/rack-scale-deployment.md) |
| Add or remove nodes on a running cluster | [Node Scaling](docs/customize/node_scaling.md) |
| Upgrade, back up and restore a layer | [Upgrade a Layer](docs/deploy/upgrade.md) |
| Use the cluster web UI and platform sign-in | [Headlamp and Platform Sign-in](docs/deploy/headlamp.md) |
| Open Grafana and read the dashboards | [Observability](docs/deploy/observability.md) |
| Install on OpenShift | [Deploy on OpenShift](docs/deploy/openshift.md) |
| Use NetApp ONTAP and Trident for storage | [NetApp ONTAP and Trident](docs/deploy/netapp_ontap.md) |
| **Tune** | |
| Place platform and inference workloads on different nodes | [Node Topology](docs/customize/node_topology.md) |
| Pin inference pods to CPU cores (NUMA-aware) | [NRI CPU Balloons](docs/customize/nri_cpu_balloons.md) |
| **Understand** | |
| What the platform is and why | [Meet Toolkit Manager](docs/meet/meet.md) |
| How it is built: layers, execution flow, repositories | [Architecture](docs/reference/architecture.md) |
| How a request reaches a model; IPs, DNS and ingress | [Network Architecture](docs/deploy/networking.md) |
| Every command and flag | [CLI Reference](docs/customize/cli.md) |
| Short answers and terms | [FAQ](docs/faq.md) · [Glossary](docs/glossary.md) |

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

*Intel®, Intel® Xeon®, and Intel® Arc™ are registered trademarks of Intel Corporation or its subsidiaries.*
