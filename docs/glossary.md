# Glossary

[← Docs Index](README.md)

Terms used across this documentation, in alphabetical order.

---

**Action.** What `es_auto_installer.sh` does to a target: `configure`, `init`, `install`, `teardown`, `validate`, `status`, or `show`. Each role implements the actions that apply to it.

**Ambient mesh.** Istio's sidecar-free mode. Traffic between pods is encrypted with mutual TLS by a per-node proxy (ztunnel) rather than by a proxy container injected into every pod.

**Bastion.** A jump host used to reach cluster nodes that are not directly reachable from where you run the installer. See [Multi-Node & BYO Cluster](deploy/topologies.md).

**BYO cluster (bring your own).** Deploying onto a Kubernetes cluster you already operate by setting `existing_kubernetes` to its kubeconfig. Cluster provisioning is skipped; everything above it still installs.

**cert-manager.** The controller that issues and renews the TLS certificates used by the gateway. With `gateway_tls_mode: selfsigned` it creates an internal CA and a wildcard certificate for `*.<base_domain_name>`.

**CNPG (CloudNativePG).** The operator that runs the PostgreSQL clusters used by platform services.

**Component.** The smallest unit the installer deploys, backed by one Ansible role, for example `istio`, `keycloak`, or `kserve`. Components are registered in the component registry with their layer and dependencies.

**Component registry.** `configs/components.yaml`, plus the `components.yaml` each external repository contributes. It is the single source of truth for what exists, which layer it belongs to, and what it depends on.

**Envoy AI Gateway.** The model-aware layer in front of the serving backends. It routes by the requested model, applies rate limits, and load balances across replicas.

**Envoy Gateway.** The Kubernetes ingress gateway that terminates TLS and admits external traffic into the cluster.

**Environment (`env/<name>/`).** One deployment's state: `global_config.yaml`, per-layer configs, inventory, kubeconfig, credentials, and logs. Selected with `--env`, defaulting to `local`.

**Flavour.** A preset that `init <layer> --flavour <name>` seeds a layer's config from. Only layers that ship presets accept it, such as the RAG pipelines.

**Idempotent.** Running the same install twice produces the same result rather than duplicating or breaking anything. Every role is written this way, which is why a failed run can simply be re-run.

**Inventory.** The Kubespray-compatible `hosts.yaml` under `env/<name>/inventory/` that lists the machines and their roles. Single-node deployments get a generated localhost inventory.

**Keycloak.** The identity provider deployed when `auth_provider: keycloak`. It issues the OIDC JWTs the gateway validates on every request and provides single sign-on and role-based access.

**KServe.** The Kubernetes model-serving framework that runs the model servers and exposes them as cluster services.

**Kubespray.** The upstream Ansible project used to provision the Kubernetes cluster. It is vendored under `.kubespray/` with its own virtual environment and inventory, and is driven by the `kubernetes` role.

**Langfuse.** LLM observability (traces, prompts, token usage), deployed with the `litellm` auth provider.

**Layer.** A group of components installed as a unit, in dependency order: `infrastructure`, `platform`, `inference`, and the opt-in `erag`. Installing a layer pulls in the layers it depends on.

**LiteLLM.** An OpenAI-compatible proxy that issues virtual API keys with per-key budgets. Deployed when `auth_provider: litellm`, in which case Keycloak is not deployed.

**MetalLB.** The load balancer that hands out external IP addresses on bare metal, where no cloud load balancer exists.

**SeaweedFS.** S3-compatible object storage used as the backend for logs, traces, and blobs.

**model-manager.** The CLI that deploys, scales, lists, and undeploys models. It downloads weights, chooses serving parameters, and creates the serving resources, so no YAML is needed.

**Model catalog (`models.yaml`).** The per-environment list of servable models with runtime, CPU and memory sizing, and server arguments. `model-manager deploy <name>` resolves names against it.

**NRI CPU balloons.** A NUMA-aware CPU pinning policy. It reserves groups of cores for inference pods so threads are not scheduled across memory domains, which matters for CPU inference throughput. Selected with `kubernetes_cpu_policy`.

**Node topology.** Soft affinity that prefers platform pods on control-plane nodes and inference pods on workers, without hard enforcement. Controlled by `node_topology_enabled`.

**NUMA (non-uniform memory access).** A property of multi-socket servers where each CPU has faster access to its own memory. Ignoring it costs inference performance, which is why CPU pinning exists.

**OpenVINO™ Model Server (OVMS).** An Intel model server, one of the two serving runtimes available alongside vLLM.

**PSA (Pod Security Admission).** The Kubernetes admission controller that enforces per-namespace security levels. Which level each namespace gets is documented in [Namespace Security Labels](reference/labels.md).

**Role.** An Ansible role under `roles/<name>/`, implementing one component. Its `tasks/main.yaml` dispatches to the file matching the action being run.

**RWX (ReadWriteMany).** A volume access mode allowing several nodes to mount the same volume. Required for multi-node deployments, since model weights must be readable from every node.

**Storage backend.** Where persistent data lives: `local-path` (single node), `nfs`, `ceph`, `netapp-trident`, or `custom` (a StorageClass your own CSI driver already provides). Set with `storage_backend`.

**Target.** What an action applies to: a layer such as `platform`, a component such as `istio`, or `erag`. Dependencies resolve automatically unless you pass `--only`.

**Virtual key.** A scoped API key issued by LiteLLM, with its own budget and rate limits, used instead of a user JWT when `auth_provider: litellm`.

**vLLM.** A high-throughput LLM serving runtime, the default for text generation on Xeon.

**ztunnel.** The per-node proxy that carries mutual TLS traffic in an Istio ambient mesh.
