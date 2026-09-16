# Meet Intel® AI for Enterprise Solutions

[← Docs index](../README.md)

Intel® AI for Enterprise Solutions is the installer for Intel® enterprise
AI toolkits. It is not a toolkit of its own, and it does not replace those
products. It is the shared install path that puts them on the same Kubernetes
foundation.

You run one command. The installer stands up that foundation (a cluster, or a
cluster you already run, plus storage, TLS, identity, a gateway, and
observability) and then deploys the Intel® enterprise AI toolkits onto it. The stack runs
on Intel® Xeon® processors, on your own infrastructure. You do not install each
toolkit by hand, and you do not rebuild Kubernetes, certificates, identity, and
observability for every toolkit.

## What it is

**One installer for the portfolio.** `./es_auto_installer.sh` is the entry
point. It creates a named environment, installs, validates, reports status, and
tears down. Each Intel® enterprise AI toolkit lives in its own repository. The installer
clones those repos and runs them in the same workflow, so you are not learning a
different toolchain per toolkit.

**A shared foundation.** Every toolkit needs the same base: a cluster, persistent
storage, TLS, identity, ingress, and observability. The installer deploys that
base once. Toolkits then plug into it instead of each bringing their own cluster
and certificates.

**Intel® enterprise AI toolkits on top.** Inference is included when you pass `--all`.
Other Intel® enterprise AI toolkits are opt-in: you add them when you need them, without
rebuilding the foundation underneath.

**CPU, on-premises.** The stack is designed for Intel® Xeon® processors. You do
not need a discrete GPU. Workloads stay on your infrastructure, which matters
when data cannot leave the building.

**Modular.** Every component can be enabled, disabled, or replaced. If you
already run cert-manager, a load balancer, or a service mesh, set the matching
flag to `false` in `global_config.yaml` and the installer skips the built-in
one.

**Isolated environments.** Each `env/<name>/` has its own config, inventory,
kubeconfig, and model catalog. You can run development, staging, and production
from one machine without those environments sharing state.

## Why assembling this yourself is hard

Each toolkit still needs a cluster, storage, TLS, identity, a gateway, and
observability. If you install those pieces once per toolkit, and try to keep
versions and certificates consistent by hand, the integration work usually takes
weeks.

This installer deploys the shared foundation first, then the toolkits, in
dependency order, from one command:

```bash
./es_auto_installer.sh install --all --env <name>
```

```
Infrastructure  (Kubernetes, storage)
      ↓
Platform        (cert-manager, Istio, MetalLB, Envoy Gateway, PostgreSQL, Keycloak, MinIO, observability)
      ↓
Inference       (KServe, Envoy AI Gateway, LiteLLM, Langfuse, model runtimes)
      ↓
Applications    (other Intel® enterprise AI toolkits, opt-in)
```

## Solution layers

| Layer | What it provides | Included in `--all` |
|---|---|---|
| **Infrastructure** | Kubernetes and storage, the cluster everything else runs on | Yes |
| **Platform** | TLS, service mesh, gateway, identity, and observability | Yes |
| **Inference** | Model serving, routing, and OpenAI-compatible endpoints | Yes |
| **Applications** | Other Intel® enterprise AI toolkits that plug into the foundation | Opt-in |

Deploy the foundation and inference today. Add another toolkit later without
rebuilding the layers below it.

## What gets installed

These are the components the installer deploys for the shared foundation and for
inference. Other Intel® enterprise AI toolkits add their own components when you opt in.

| Component | Purpose |
|---|---|
| **Kubernetes** | Orchestration for every workload |
| **Storage** | Persistent volumes for model weights (local-path, NFS, or Ceph) |
| **Cert-Manager** | Issues and rotates TLS certificates |
| **Istio (Ambient)** | mTLS service mesh without sidecars |
| **MetalLB** | LoadBalancer IPs on bare metal |
| **Envoy Gateway** | HTTPS ingress, TLS termination, and JWT auth |
| **PostgreSQL (CNPG)** | Database for Keycloak, LiteLLM, and Langfuse |
| **Keycloak** | OIDC identity, including SSO and RBAC |
| **MinIO** | S3-compatible object store |
| **Observability** | Prometheus, Grafana, Loki, Tempo, and OpenTelemetry |
| **Envoy AI Gateway** | Model-aware routing and rate limiting |
| **KServe** | Model serving (InferenceService and LLMInferenceService) |
| **LiteLLM** | OpenAI-compatible proxy and virtual keys |
| **Valkey** | In-memory cache for LiteLLM and Langfuse |
| **Langfuse** | LLM traces, token usage, and cost |
| **NRI CPU Balloons** | NUMA-aware CPU pinning for vLLM |

How those pieces are wired, and how a request flows, is in
[Architecture & Design](architecture.md) and
[Network Architecture](../deploy/networking.md).

## Deployment options

| Path | How | Best for |
|---|---|---|
| **Single node** | Installs Kubernetes on localhost | First trial, evaluation |
| **Multi-node** | Provisions Kubernetes on remote nodes over SSH | Production and scale-out |
| **Existing Kubernetes** | Skips Kubespray and deploys onto your cluster | Managed or brownfield clusters |

## Next steps

1. **[Prerequisites](../quickstart/prerequisites.md)**: OS, hardware, sudo, and network.
2. **[Getting Started](../quickstart/quickstart.md)**: pick a path and install.
3. **[Deploy a Model](../deploy/deploy_models.md)**: serve an LLM with `model-manager`.

Also see the [FAQ](../quickstart/faq.md),
[Integration Guide](../customize/integration.md),
[Configuration Reference](../customize/configuration.md), and
[Architecture & Design](architecture.md).
