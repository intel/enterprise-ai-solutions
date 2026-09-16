# Intel® AI for Enterprise Solutions Documentation

Intel® AI for Enterprise Solutions is the installer for Intel® enterprise
AI toolkits. It is not a toolkit of its own. One command stands up a shared
Kubernetes foundation and deploys those toolkits onto it, on Intel® Xeon®
processors, on your own infrastructure.

You do not install each toolkit by hand, and you do not wire Kubernetes, TLS,
identity, and observability yourself for every product. The installer does that
once. Toolkits then plug into the same cluster, gateway, and identity.

## Choose how to run it

The same installer covers three paths. What changes is where Kubernetes comes
from, not which CLI you learn.

| Path | Best for | Guide |
|------|----------|-------|
| **Single node** | Trying it out on the machine you are sitting on | [Getting Started](quickstart/quickstart.md) |
| **Multi-node** | Production, or more capacity than one box | [Multi-Node & BYO Cluster](deploy/topologies.md) |
| **Existing Kubernetes** | You already have a cluster | [Multi-Node & BYO Cluster](deploy/topologies.md) |

## Prerequisites

Do this once, on the machine that will run the installer, before the first
`./es_auto_installer.sh install`. Full steps:
**[Prerequisites](quickstart/prerequisites.md)**.

## At a glance

| | |
|---|---|
| **What you run** | One installer that deploys a shared Kubernetes foundation and Intel® enterprise AI toolkits on top |
| **What inference exposes** | An OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`) |
| **Hardware** | Intel® Xeon® CPU. No GPU required. |
| **Installer** | `./es_auto_installer.sh` |
| **Model CLI** | `./model-manager` (inference toolkit) |
| **Auth** | Keycloak JWT (default) or LiteLLM virtual keys |
| **Clients that work unchanged** | OpenAI SDK, LangChain, LlamaIndex, Cursor, n8n. Change the base URL and pass a token. |

## Start here

This path deploys the foundation and the inference toolkit on **one machine**,
then serves **qwen3-0-6b**. That model does not need a Hugging Face token. The
full walkthrough, including how you authenticate the request, is in the
[project README Quick Start](../README.md#quick-start).

`configure` installs the tools the installer needs (Python, yq, kubectl, helm).
`init local` creates an isolated environment under `env/local/`. `install --all`
deploys the foundation and inference (about 15 to 20 minutes).

```bash
git clone https://github.com/intel/enterprise-ai-solutions.git
cd enterprise-ai-solutions

./es_auto_installer.sh configure          # once per machine
./es_auto_installer.sh init local
./es_auto_installer.sh install --all      # about 15-20 min
```

When install finishes, point `kubectl` at the new cluster and deploy a model.
`model-manager` downloads the weights, starts the serving pod, and prints an
endpoint.

```bash
export KUBECONFIG=$(pwd)/env/local/kubeconfig.yaml
./model-manager deploy qwen3-0-6b --wait
```

If you need several machines, or a cluster you already run, start at
[Getting Started](quickstart/quickstart.md) instead of this single-node path.

## Documentation

**Overview**

- **[Meet Intel® AI for Enterprise Solutions](meet/meet.md)**: what the installer is, the shared foundation, and how Intel® enterprise AI toolkits plug in
- **[Architecture & Design](meet/architecture.md)**: how the installer, repos, and layers fit together

**Getting started**

- **[Prerequisites](quickstart/prerequisites.md)**: OS, hardware, sudo, network, and Hugging Face token
- **[Getting Started](quickstart/quickstart.md)**: single node, multi-node, or bring-your-own cluster
- **[FAQ](quickstart/faq.md)**: answers to the questions people ask first, with pointers to the full guides

**Deploy**

- **[Deployment Guide](deploy/install_platform.md)**: full install, first model, and how you authenticate
- **[Deploy a Model](deploy/deploy_models.md)**: `model-manager` catalog, gated models, scale, and undeploy
- **[Multi-Node & BYO Cluster](deploy/topologies.md)**: several machines, or a cluster you already run
- **[Network Architecture](deploy/networking.md)**: how a request reaches a model
- **[Namespace Security Labels](deploy/labels.md)**: Pod Security Admission and Istio labels per namespace

**Configure**

- **[Configuration Reference](customize/configuration.md)**: every option in `global_config.yaml`
- **[CLI Reference](customize/cli.md)**: `es_auto_installer.sh` and `model-manager` commands
- **[Integration Guide](customize/integration.md)**: OpenAI SDK, LangChain, LlamaIndex, and other clients
- **[Node Topology](customize/node_topology.md)**: platform pods vs inference pods on multi-node clusters
- **[NRI CPU Balloons](customize/nri_cpu_balloons.md)**: NUMA-aware CPU pinning for inference
