# Intel® AI for Enterprise Solutions Documentation

The technical documentation for deploying and operating the platform. Start with
**Meet** for the what and why, **Get started** to stand it up, use the **Deploy**
guides to take it further, then configure and look things up as needed.

> Looking for the three-step quick start? It's in the [project README](../README.md#quick-start).

## Meet

| Guide | What it covers |
|-------|----------------|
| [Meet Intel® AI for Enterprise Solutions](meet/meet.md) | What the platform is, the problem it solves, and the layers it deploys |
| [FAQ](faq.md) | Short answers to the questions that come up most |
| [Glossary](glossary.md) | Every term used in these guides, defined once |

## Get started

Check your machine meets the bar, then deploy it.

| Guide | What it covers |
|-------|----------------|
| [Prerequisites](quickstart/prerequisites.md) | Everything needed before running the installer |
| [Getting Started](quickstart/quickstart.md) | Three deployment paths — pick one and follow it end to end |
| [Getting Started with RAG](quickstart/getting_started_rag.md) | Deploy Intel® AI for Enterprise RAG (vector DB, document ingestion, chat UI) on top of inference |

## Deploy

Install the platform, serve a model, deploy RAG, and go beyond a single node.

| Guide | What it covers |
|-------|----------------|
| [Deployment Guide](deploy/install_platform.md) | Step-by-step platform install — no Kubernetes experience needed |
| [Deploy a Model](deploy/deploy_models.md) | Deploying, accessing, and managing LLM inference with `model-manager` |
| [Multi-Node & BYO Cluster](deploy/topologies.md) | Deploying across several machines, or onto an existing cluster |
| [NetApp ONTAP and Trident](deploy/netapp_ontap.md) | Deploying with Trident + ONTAP as the storage backend |
| [Network Architecture](deploy/networking.md) | Network topology, IP allocation, and how a request reaches a model |

## Configure & customize

Tune the deployment: change any setting, place workloads across nodes, and
connect your own applications.

| Guide | What it covers |
|-------|----------------|
| [Configuration Reference](customize/configuration.md) | Every option in `global_config.yaml`, and how to override them |
| [Integration Guide](customize/integration.md) | Connecting your tool or framework without modifying the stack |
| [Node Topology & Workload Placement](customize/node_topology.md) | Separating platform and inference workloads on multi-node clusters |
| [NRI CPU Balloons](customize/nri_cpu_balloons.md) | NUMA-aware CPU pinning for inference pods |

## Reference

Look up exact commands, architecture decisions, and cluster-level details.

| Guide | What it covers |
|-------|----------------|
| [CLI Reference](customize/cli.md) | `es_auto_installer.sh` and `model-manager` commands and flags |
| [Architecture & Design](reference/architecture.md) | How the modular deployment framework is structured |
| [Namespace Security Labels](reference/labels.md) | Pod Security Admission and Istio labels applied per namespace |
| [Adding Solution Layers](reference/adding_solutions.md) | How to create and plug in your own solution layer |
