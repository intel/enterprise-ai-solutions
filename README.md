# Intel® AI for Enterprise Solutions

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Platform: Intel Xeon](https://img.shields.io/badge/Platform-Intel%C2%AE%20Xeon%C2%AE-0068B5)](https://www.intel.com/xeon)
[![Deployment: Kubernetes](https://img.shields.io/badge/Deployment-Kubernetes-326CE5)](https://kubernetes.io)
[![Serving: vLLM · OVMS](https://img.shields.io/badge/Serving-vLLM%20%C2%B7%20OVMS-purple)](https://vllm.ai)
[![Gateway: Envoy](https://img.shields.io/badge/Gateway-Envoy%20AI%20Gateway-orange)](https://gateway.envoyproxy.io)
[![API: OpenAI Compatible](https://img.shields.io/badge/API-OpenAI%20Compatible-green)](https://platform.openai.com/docs/api-reference)

**The fast path to deploying enterprise AI on Intel® Xeon® silicon. Go from bare metal to a working AI stack in minutes.**

> Deploy and connect LLM inference, RAG, and agentic workflows in a customizable Kubernetes-based platform — with a GenAI gateway, security, intelligent routing, observability, model serving, and infrastructure automation built in.

---

## What is Intel® AI for Enterprise Solutions?

Enterprise AI needs more than a model endpoint. Teams need to connect LLM inference, RAG, and agentic workflows, along with security, routing, and observability.

Intel® AI for Enterprise Solutions wires these pieces together in a pre-integrated, customizable Kubernetes stack optimized for Intel® Xeon® processors.

A single installer script, `es_auto_installer.sh`, takes bare-metal nodes through Kubernetes provisioning, platform services (networking, security, observability), model serving, and AI gateway setup — delivering a production-ready AI platform with OpenAI-compatible endpoints in one run.

The end result: teams get a secured, load-balanced stack where they can immediately serve LLM models, run RAG pipelines, and monitor everything through a unified gateway. Start with defaults, then configure or extend as needed.

> Want the full picture? See [Architecture](docs/reference/architecture.md) and [Meet AI for Enterprise Solutions](docs/meet/meet.md).

## Architecture

The stack installs in four ordered layers, in the order they are deployed:

| # | Layer | Components | Source | Documentation |
| --- | --- | --- | --- | --- |
| 1 | **Infrastructure** | Kubernetes (via Kubespray) · Storage (local-path · NFS · Ceph · NetApp ONTAP) · Intel® Xeon® | [enterprise-ai-solutions](https://github.com/intel/enterprise-ai-solutions) | [Deployment Guide](docs/deploy/install_platform.md) |
| 2 | **Platform** | Istio ambient mesh, Envoy Gateway, PostgreSQL, Keycloak, SeaweedFS, observability | [enterprise-ai-solutions](https://github.com/intel/enterprise-ai-solutions) | [Deployment Guide](docs/deploy/install_platform.md) |
| 3 | **Inference** | Envoy AI Gateway, KServe, vLLM / OpenVINO™ Model Server — exposes the model endpoint on top of which services like RAG and agents can be built | [enterprise-inference](https://github.com/intel/enterprise-inference) | [Deploy a Model](docs/deploy/deploy_models.md) |
| 4 | **Intel® AI for Enterprise RAG** *(opt-in)* | Vector database, document ingestion (EDP), RAG pipeline orchestration (GMC), MCP gateway, chat history, web UI — requires `init erag` and `install erag` | [enterprise-rag](https://github.com/intel/enterprise-rag) | [Getting Started with RAG](docs/quickstart/getting_started_rag.md) |

**Request flow:** a request enters through the Envoy AI Gateway, is authenticated against Keycloak (or a LiteLLM virtual key), and is routed to the matching model-serving backend. Every layer's health and latency is visible in the built-in Grafana / Prometheus / Loki / Tempo stack.

<p align="center">
  <img src="docs/assets/architecture.png" alt="Intel AI for Enterprise Solutions layer diagram: infrastructure layer (Kubernetes, storage) at the base; platform layer (Istio, Envoy Gateway, PostgreSQL, Keycloak, SeaweedFS, observability) above it; inference layer (Envoy AI Gateway, KServe, vLLM and OpenVINO Model Server) above that; and an opt-in application layer (RAG pipelines, UI, vector databases) on top, with client requests flowing through the gateway to the serving layer" />
</p>

> See the [Architecture deep-dive](docs/reference/architecture.md) for the full component list, execution flow, and cross-repo layering.

---

## Quick Start

One node, defaults, and roughly 20 minutes from bare metal to a working stack. Each target pulls in the layers below it automatically, so the target name is the only thing that changes between use cases.

> [!NOTE]
> **Prerequisites:** Ubuntu 22.04/24.04 (or RHEL/Rocky x86_64), passwordless sudo, and internet access. Full list → [Prerequisites](docs/quickstart/prerequisites.md).

### Step 1 - Pick your target

| You want | Target | Then follow |
|---|---|---|
| An OpenAI-compatible LLM endpoint to build on | `inference` | [Deploy a Model](docs/deploy/deploy_models.md) |
| A complete RAG application: document ingestion, vector search, chat UI | `erag` | [Getting Started with RAG](docs/quickstart/getting_started_rag.md) |
| To connect your own app, agent, or framework to the endpoint | `inference` | [Integration Guide](docs/customize/integration.md) |

### Step 2 - Install

```bash
git clone https://github.com/intel/enterprise-ai-solutions.git
cd enterprise-ai-solutions

./es_auto_installer.sh configure          # one-time machine prep (Python 3.11+, yq, kubectl, helm)
./es_auto_installer.sh init inference     # swap in your target from Step 1
./es_auto_installer.sh install inference  # deploys the target plus everything it depends on (~15-20 min)
```

Point `kubectl` at the new cluster:

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
kubectl get nodes   # should show Ready
```

All settings live in `env/local/global_config.yaml`, created by `init`. Defaults are a single local node with Keycloak OIDC auth. See [Configuration Reference](docs/customize/configuration.md) for every option, and [Deployment Guide](docs/deploy/install_platform.md) for multi-node, bastion, bring-your-own-cluster, and the alternative `litellm` virtual-key auth mode.

> [!TIP]
> `--env` defaults to `local`. Install and teardown are environment-scoped: what you installed with `--env prod` must be torn down with `--env prod`. Remove everything (config preserved) with `./es_auto_installer.sh teardown infrastructure`.

### Step 3 - Serve a model and call it

`model-manager` downloads the weights, picks serving parameters, and creates the pod - no YAML needed:

```bash
./model-manager deploy qwen3-0-6b --wait
```

The helper script discovers the gateway address and Keycloak credentials from the cluster and exports a JWT:

```bash
source ./ext/enterprise.ai-inference/model_manager/scripts/get-keycloak-token.sh
# Exports: TOKEN, GATEWAY_IP, GATEWAY_DOMAIN

curl -sk --noproxy '*' \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  --resolve "$GATEWAY_DOMAIN:443:$GATEWAY_IP" \
  -d '{"model":"qwen3-0-6b","messages":[{"role":"user","content":"Hello!"}],"max_tokens":64}' \
  "https://$GATEWAY_DOMAIN/v1/chat/completions"
```

Everything speaks the OpenAI-compatible API, so any client that works with OpenAI works here. Gated models, ad-hoc Hugging Face deployments, the Python SDK, and the `litellm` access flow are covered in [Deploy a Model](docs/deploy/deploy_models.md). Building the RAG application instead? Continue with [Getting Started with RAG](docs/quickstart/getting_started_rag.md).

---

## Advanced

The Quick Start gets you running with single-node defaults. From here you can tailor almost everything — TLS and authentication, multi-node or bring-your-own cluster, storage backends, RAG pipelines, model catalogs, autoscaling, proxies, and which components to enable or swap.

| Goal | Guide |
|---|---|
| All configuration options | [Configuration Reference](docs/customize/configuration.md) |
| Multi-node cluster or BYO Kubernetes | [Topologies](docs/deploy/topologies.md) |
| Deploy and manage models | [Deploy Models](docs/deploy/deploy_models.md) |
| Deploy the RAG layer | [Getting Started with RAG](docs/quickstart/getting_started_rag.md) |
| Connect your app or framework | [Integration Guide](docs/customize/integration.md) |
| CLI commands and flags | [CLI Reference](docs/customize/cli.md) |
| Architecture deep-dive | [Architecture](docs/reference/architecture.md) |
| Common questions | [FAQ](docs/faq.md) |
| Terminology | [Glossary](docs/glossary.md) |
| Network topology and ingress | [Network Architecture](docs/deploy/networking.md) |
| Trident + ONTAP | [NetApp ONTAP and Trident](docs/deploy/netapp_ontap.md) |
| Workload placement (multi-node) | [Node Topology](docs/customize/node_topology.md) |
| NUMA-aware CPU pinning | [NRI CPU Balloons](docs/customize/nri_cpu_balloons.md) |

---

## License

Licensed under the [Apache License, Version 2.0](LICENSE).

## Links

- [Documentation Index](docs/README.md)
- [GitHub Repository](https://github.com/intel/enterprise-ai-solutions)
- [Intel® Enterprise for AI Inference (inference layer)](https://github.com/intel/enterprise-inference)
- [Intel® AI for Enterprise RAG (RAG layer)](https://github.com/intel/enterprise-rag)
- [Meet AI for Enterprise Solutions](docs/meet/meet.md)
- [Architecture](docs/reference/architecture.md)

---

*Intel®, Intel® Xeon®, and Intel® Arc™ are registered trademarks of Intel Corporation or its subsidiaries. Licensed under the Apache License, Version 2.0.*
