# Toolkit Manager

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Platform: Intel Xeon](https://img.shields.io/badge/Platform-Intel%C2%AE%20Xeon%C2%AE-0068B5)](https://www.intel.com/xeon)
[![Deployment: Kubernetes](https://img.shields.io/badge/Deployment-Kubernetes-326CE5)](https://kubernetes.io)
[![Serving: vLLM · OVMS](https://img.shields.io/badge/Serving-vLLM%20%C2%B7%20OVMS-purple)](https://vllm.ai)
[![Gateway: Envoy](https://img.shields.io/badge/Gateway-Envoy%20AI%20Gateway-orange)](https://gateway.envoyproxy.io)
[![API: OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20Compatible-green)](https://platform.openai.com/docs/api-reference)

**The fast path to deploying AI on Intel® Xeon® silicon. Go from bare metal to a working AI stack in minutes.**

> Deploy and connect LLM inference, RAG, and agentic workflows in a customizable Kubernetes-based platform — with a GenAI gateway, security, intelligent routing, observability, model serving, and infrastructure automation built in.

---

## What is Toolkit Manager?

A production AI stack needs more than a model endpoint. Teams need to connect LLM inference, RAG, and agentic workflows, along with security, routing, and observability.

Toolkit Manager wires these pieces together in a pre-integrated, customizable Kubernetes stack optimized for Intel® Xeon® processors.

A single installer script, `toolkit_manager.sh`, takes bare-metal nodes through Kubernetes provisioning, platform services (networking, security, observability), model serving, and AI gateway setup — delivering a production-ready AI platform with OpenAI-compatible endpoints in one run.

The end result: teams get a secured, load-balanced stack where they can immediately serve LLM models, run RAG pipelines, and monitor everything through a unified gateway. Start with defaults, then configure or extend as needed.

> Want the full picture? See [Architecture](docs/reference/architecture.md) and [Meet Toolkit Manager](docs/meet/meet.md).

## Architecture

The stack installs in four ordered layers, in the order they are deployed:

| # | Layer | Components | Source | Documentation |
| --- | --- | --- | --- | --- |
| 1 | **Infrastructure** | Kubernetes (via Kubespray) · Storage (local-path · NFS · Ceph · NetApp ONTAP · your own CSI driver) | [Toolkit Manager](https://github.com/intel/toolkit-manager) | [Deployment Guide](docs/deploy/install_platform.md) |
| 2 | **Platform** | cert-manager, Istio ambient mesh, MetalLB, Envoy Gateway, PostgreSQL, Keycloak, SeaweedFS, observability (Grafana, Prometheus, Loki, Tempo) | [Toolkit Manager](https://github.com/intel/toolkit-manager) | [Deployment Guide](docs/deploy/install_platform.md) |
| 3 | **Intel® Inference Toolkit** | Envoy AI Gateway, KServe, vLLM / OpenVINO™ Model Server, optional LiteLLM and Langfuse — exposes the model endpoint that RAG and agents build on | [Inference Toolkit](https://github.com/intel/inference-toolkit) | [Deploy a Model](docs/deploy/deploy_models.md) |
| 4 | **Intel® Chatbot Toolkit** *(opt-in)* | Vector database, document ingestion (EDP), RAG pipeline orchestration (GMC), MCP gateway, chat history, web UI — requires `init erag` and `install erag` | [Chatbot Toolkit](https://github.com/intel/chatbot-toolkit) | [Getting Started with Intel® Chatbot Toolkit](docs/quickstart/getting_started_rag.md) |
| 4 | **Intel® Agent Toolkit** *(opt-in)* | Agent Sandbox (optional Kata microVMs), OpenShell, Redis Stack, pgvector, KubeRay, LiteLLM virtual keys, guardrail and hybrid router — requires `init agent-toolkit` and `install agent-toolkit` | [Agent Toolkit](https://github.com/intel/agent-toolkit) | [Getting Started with the Intel® Agent Toolkit](docs/quickstart/getting_started_agent_toolkit.md) |

**Request flow:** a request enters through Envoy Gateway, which checks its credential (a Keycloak token, or a LiteLLM virtual key), and the Envoy AI Gateway then routes it to the server running the requested model. Every layer's health and latency is visible in the built-in Grafana / Prometheus / Loki / Tempo stack.

<p align="center">
  <img src="docs/assets/toolkit-manager-architecture.png" alt="Toolkit Manager architecture: Toolkit Manager is the core repository and one installer (git clone, then the installer, then the solutions). It auto-deploys a platform with inference, RAG, and agents. Four blocks stack upward: Infrastructure (Kubernetes, Intel infrastructure, OS and drivers, bring-your-own cluster) at the base; Platform (gateway, auth, routing, observability, serving, storage) builds on infrastructure; Model Inference (Intel® Inference Toolkit and Intel® Inference Microservices) builds on the platform; Solutions requires inference. Chatbot/RAG (Intel® Chatbot Toolkit) and Agents (Intel® Agent Toolkit) are marked coming soon." />
</p>

> See the [Architecture deep-dive](docs/reference/architecture.md) for the full component list, execution flow, and cross-repo layering.

---

## Quick Start

One machine, default settings, three steps. Each target pulls in the layers below it automatically, so the target name is the only thing that changes between use cases. Allow up to an hour for the first install: most of that time is spent downloading container images.

> [!NOTE]
> **Prerequisites:** Ubuntu 22.04/24.04 (or RHEL/Rocky Linux), `git`, `sudo` rights (passwordless is easiest), and internet access. Full list → [Prerequisites](docs/quickstart/prerequisites.md).

### Step 1 - Pick your target

| You want | Target | Then follow |
|---|---|---|
| An OpenAI-compatible LLM endpoint to build on | `inference` | [Deploy a Model](docs/deploy/deploy_models.md) |
| A complete RAG application: document ingestion, vector search, chat UI | `erag` | [Getting Started with Intel® Chatbot Toolkit](docs/quickstart/getting_started_rag.md) |
| Agent sandboxes, memory stores, and a governed LLM gateway for agents | `agent-toolkit` | [Getting Started with the Intel® Agent Toolkit](docs/quickstart/getting_started_agent_toolkit.md) |
| To connect your own app, agent, or framework to the endpoint | `inference` | [Integration Guide](docs/customize/integration.md) |

### Step 2 - Install

Run these commands on the machine that will become the cluster:

```bash
git clone https://github.com/intel/toolkit-manager.git
cd toolkit-manager

./toolkit_manager.sh configure          # 1. one-time machine prep; answer y to the prompt
./toolkit_manager.sh init inference     # 2. create env/local/; swap in your target from Step 1
./toolkit_manager.sh install inference  # 3. deploy the target and everything it depends on
```

1. `configure` uses `sudo` to install Python 3.11+, `yq`, `kubectl` and `helm`, and creates the installer's Python environment in `.venv/`. It asks before changing anything. Skip it if those tools are already installed.
2. `init` creates the environment folder `env/local/` with your settings files and downloads the repositories the target needs into `ext/`.
3. `install` builds the Kubernetes cluster and installs every layer up to the target. A successful run ends with an `INSTALLATION COMPLETE` banner that names the log file under `env/local/logs/`.

Point `kubectl` at the new cluster and check it:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes                  # the node shows STATUS Ready
./toolkit_manager.sh status        # what is installed, and the service URLs
```

Settings live in `env/local/global_config.yaml` and, for the inference layer (including `auth_type`), `env/local/config.inference.yaml`, both created by `init`. The defaults are a single local node, the domain `inteltoolkits.ai`, and Keycloak tokens (`auth_type: jwt`) for API access. See [Configuration Reference](docs/customize/configuration.md) for every option, and the [Deployment Guide](docs/deploy/install_platform.md) for multi-node, bastion, bring-your-own-cluster, and the alternative `litellm_vkey` (LiteLLM virtual key) auth type.

> [!TIP]
> `--env` defaults to `local`. Install and teardown are environment-scoped: what you installed with `--env prod` must be torn down with `--env prod`. Remove everything (your config files are kept) with `./toolkit_manager.sh teardown infrastructure`.

### Step 3 - Serve a model and call it

`model-manager` downloads the weights, picks serving parameters, and creates the pod - no YAML needed:

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

A JSON reply with a `choices` list means the model is serving. `--resolve` sends the request to the gateway IP without an `/etc/hosts` entry, and `--cacert` trusts the installer's self-signed certificate authority.

Everything speaks the OpenAI-compatible API, so any client that works with OpenAI works here. Gated models, ad-hoc Hugging Face deployments, the Python SDK, and the `litellm` access flow are covered in [Deploy a Model](docs/deploy/deploy_models.md). Building the RAG application instead? Continue with [Getting Started with Intel® Chatbot Toolkit](docs/quickstart/getting_started_rag.md).

---

## Advanced

The Quick Start gets you running with single-node defaults. From here you can tailor almost everything — TLS and authentication, multi-node or bring-your-own cluster, storage backends, RAG pipelines, model catalogs, autoscaling, proxies, and which components to enable or swap.

| Goal | Guide |
|---|---|
| All configuration options | [Configuration Reference](docs/customize/configuration.md) |
| Multi-node cluster or BYO Kubernetes | [Topologies](docs/deploy/topologies.md) |
| Rack-scale: a cluster on a dedicated high-speed fabric | [Rack-scale Deployment](docs/rack-scale-deployment.md) |
| Deploy and manage models | [Deploy Models](docs/deploy/deploy_models.md) |
| Deploy Intel® Chatbot Toolkit | [Getting Started with Intel® Chatbot Toolkit](docs/quickstart/getting_started_rag.md) |
| Connect your app or framework | [Integration Guide](docs/customize/integration.md) |
| CLI commands and flags | [CLI Reference](docs/customize/cli.md) |
| Architecture deep-dive | [Architecture](docs/reference/architecture.md) |
| Common questions | [FAQ](docs/faq.md) |
| Terminology | [Glossary](docs/glossary.md) |
| Network topology and ingress | [Network Architecture](docs/deploy/networking.md) |
| Trident + ONTAP | [NetApp ONTAP and Trident](docs/deploy/netapp_ontap.md) |
| Workload placement (multi-node) | [Node Topology](docs/customize/node_topology.md) |
| NUMA-aware CPU pinning | [NRI CPU Balloons](docs/customize/nri_cpu_balloons.md) |
| Add or remove nodes from a running cluster | [Node Scaling](docs/customize/node_scaling.md) |
| Upgrade, back up and restore a layer | [Upgrade a Layer](docs/deploy/upgrade.md) |

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

## Links

- [Documentation Index](docs/README.md)
- [GitHub Repository](https://github.com/intel/toolkit-manager)
- [Intel® Inference Toolkit](https://github.com/intel/inference-toolkit)
- [Intel® Chatbot Toolkit](https://github.com/intel/chatbot-toolkit)
- [Intel® Agent Toolkit](https://github.com/intel/agent-toolkit)
- [Meet Toolkit Manager](docs/meet/meet.md)
- [Architecture](docs/reference/architecture.md)

---

*Intel®, Intel® Xeon®, and Intel® Arc™ are registered trademarks of Intel Corporation or its subsidiaries. Licensed under the Apache License, Version 2.0.*
