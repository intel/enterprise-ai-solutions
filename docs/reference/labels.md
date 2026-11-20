# Namespace Security Labels

[← Docs Index](../README.md)

Every namespace (a named group of Kubernetes resources) that Toolkit Manager creates gets up to four labels. This page lists them per namespace, so you can see how strict each one is and whether it is in the service mesh.

- **Pod Security Admission (PSA)** labels tell Kubernetes how strict to be about the pods in a namespace (see [Pod Security Standards](https://kubernetes.io/docs/concepts/security/pod-security-standards/)).
- The **Istio ambient** label puts every pod in the namespace into the mesh, which encrypts the traffic between services (mTLS) without a sidecar container.

The values below come from the roles that create each namespace (the `Label namespace` tasks in `roles/<role>/tasks/`). To see what a running cluster actually has:

```bash
kubectl get namespaces -L pod-security.kubernetes.io/enforce,istio.io/dataplane-mode
```

## When the labels are applied

| Label | Applied when | Default |
|---|---|---|
| `pod-security.kubernetes.io/enforce`, `audit`, `warn` | `enforce_pss: true` in `global_config.yaml` | `true` |
| `istio.io/dataplane-mode: ambient` | `istio_enabled: true` in `global_config.yaml` | `true` |

With either setting turned off, the matching labels are not set. Two namespaces are exceptions: `model-manager` always gets its PSA labels, and `lws-system` gets them only when Toolkit Manager creates it (see the inference table).

On OpenShift, Istio is not installed, and the `keycloak`, `postgresql`, `cnpg-system` and `envoy-gateway-system` PSA labels are skipped: OpenShift controls pod security with its own Security Context Constraints.

## Legend

| Column | Values |
|---|---|
| **PSA enforce** | `privileged` / `baseline` / `restricted`: pods that break the profile are **rejected** |
| **PSA audit** | the same profiles: violations are written to the API server audit log, not blocked |
| **PSA warn** | the same profiles: violations are shown as warnings to whoever applies the pod |
| **Istio** | `ambient` = in the mesh / — = never in the mesh |

---

## Platform and infrastructure layers (this repo)

| Namespace | PSA enforce | PSA audit | PSA warn | Istio | Role | Created when |
|---|---|---|---|---|---|---|
| `istio-system` | `privileged` | `privileged` | `privileged` | `ambient` | `istio` | `istio_enabled: true` |
| `cert-manager` | `restricted` | `restricted` | `restricted` | `ambient` | `cert_manager` | always |
| `envoy-gateway-system` | `restricted` | `restricted` | `restricted` | `ambient` | `envoy_gateway` | Toolkit Manager installs Envoy Gateway |
| `metallb-system` | `privileged` | `privileged` | `privileged` | — | `metallb` | `metallb_enabled: true` |
| `cnpg-system` | `restricted` | `restricted` | `restricted` | `ambient` | `postgresql` | always |
| `postgresql` | `restricted` | `restricted` | `restricted` | `ambient` | `postgresql` | always |
| `keycloak` | `baseline` | `restricted` | `restricted` | `ambient` | `keycloak` | `keycloak_enabled: true` |
| `monitoring` | `privileged` | `privileged` | `privileged` | `ambient` | `observability`, `seaweedfs`, `gpu_observability` | `observability_enabled: true` |
| `metrics-server` | `restricted` | `restricted` | `restricted` | `ambient` | `metrics_server` | `metrics_server_enabled: true` |
| `node-feature-discovery` | `privileged` | `privileged` | `privileged` | — | `node_feature_discovery` | `nfd_enabled: true` |
| `headlamp` | `restricted` | `restricted` | `restricted` | `ambient` | `headlamp` | `headlamp_enabled: true` |
| `keda` | `restricted` | `restricted` | `restricted` | `ambient` | `keda` | `keda_enabled: true` |
| `velero` | `baseline` | `baseline` | `baseline` | `ambient` | `velero` | `velero_enabled: true` |
| `csi-driver-nfs` | `privileged` | `privileged` | `privileged` | — | `nfs_storage` | `storage_backend: nfs` |
| `rook-ceph` | `privileged` | `privileged` | `privileged` | — | `rook_ceph_storage` | `storage_backend: ceph` |

The `postgresql` role also labels the namespace of each app that gets a database (`keycloak`, and `litellm` / `langfuse` when they are enabled) `restricted` + `ambient`. The `keycloak` role then lowers its own namespace to `baseline` enforce, as shown above.

The NRI CPU balloons and Kata Containers components install into `kube-system`, and the NVIDIA GPU Operator (rack-scale only) into `gpu-operator`. The installer sets no labels on those namespaces.

---

## Inference layer (inference-toolkit repo)

| Namespace | PSA enforce | PSA audit | PSA warn | Istio | Role | Created when |
|---|---|---|---|---|---|---|
| `envoy-ai-gateway-system` | `privileged` | `privileged` | `privileged` | `ambient` | `envoy_ai_gateway` | `ai_gateway_provider: envoy` (default) |
| `agentgateway-system` | `privileged` | `privileged` | `privileged` | `ambient` | `agentgateway` | `ai_gateway_provider: agentgateway` |
| `kserve` | `baseline` | `restricted` | `restricted` | `ambient` | `kserve` | `kserve_enabled: true` |
| `lws-system` | `baseline` | `restricted` | `restricted` | `ambient` | `kserve` | LeaderWorkerSet is not installed yet |
| `llm-inference` | `restricted` | `restricted` | `restricted` | `ambient` | `llm_services` | `kserve_enabled: true` |
| `model-manager` | `restricted` | `restricted` | `restricted` | `ambient` | `llm_services` | Headlamp model deploy is on (`headlamp_enabled: true`) |
| `litellm` | `restricted` | `restricted` | `restricted` | `ambient` | `litellm` | `auth_type: litellm_vkey` or `litellm_enabled: true` |
| `langfuse` | `restricted` | `restricted` | `restricted` | `ambient` | `langfuse` | LiteLLM is on and `langfuse_enabled: true` |
| `clickhouse-operator-system` | `restricted` | `restricted` | `restricted` | `ambient` | `langfuse` | same as `langfuse` |
| `vllm-semantic-router-system` | `baseline` with the dashboard, else `restricted` | `restricted` | `restricted` | — | `vllm_sr` | `vllm_sr_enabled: true` |

---

## Intel® Chatbot Toolkit (enterprise-rag repo)

These namespaces only exist once `install erag` has run; `install inference` does not create them. The eRAG repo sets their labels, so check that repo for the current values.

| Namespace | PSA enforce | PSA audit | PSA warn | Istio | Role |
|---|---|---|---|---|---|
| `rag-ui` | `restricted` | `restricted` | `restricted` | `ambient` | `app_ui` |
| `audio` | `restricted` | `restricted` | `restricted` | `ambient` | `app_audio` |
| `chat-history` | `restricted` | `restricted` | `restricted` | `ambient` | `app_chat_history` |
| `chatqa` ¹ | `restricted` | `restricted` | `restricted` | `ambient` | `app_pipeline` |
| `edp` | `restricted` | `restricted` | `restricted` | `ambient` | `app_edp` |
| `seaweedfs` | `privileged` | `privileged` | `privileged` | `ambient` | `app_edp` |
| `fingerprint` | `restricted` | `restricted` | `restricted` | `ambient` | `app_fingerprint` |
| `vdb` | `restricted` | `restricted` | `restricted` | `ambient` | `app_vector_databases` |
| `system` | `restricted` | `restricted` | `restricted` | — | `app_pipeline` |
| `erag-gateway` | `privileged` | `privileged` | `privileged` | `ambient` | `app_gmc` |
| `auth-apisix` | `privileged` | `privileged` | `privileged` | `ambient` | `app_apisix` |
| `monitoring-traces` | `privileged` | `privileged` | `privileged` | `ambient` | `app_telemetry` |
| `nri-balloons-controller` | `privileged` | `privileged` | `privileged` | — | `app_nri_balloons` |

¹ The pipeline namespace is named after the pipeline type; `chatqa` is the default.

---

## Namespaces the installer does not label

| Namespace | Notes |
|---|---|
| `default` | Not used by Toolkit Manager workloads |
| `kube-system` | Kubernetes internals; exempt from PSA by convention |
| `kube-public` / `kube-node-lease` | Kubernetes internals |

---

## Why each PSA profile

| Profile | Used for |
|---|---|
| `privileged` | Pods that need host access or extra Linux capabilities: the mesh data plane (istio-cni, ztunnel), MetalLB's speaker (raw ARP sockets on the host network), Node Feature Discovery and node-exporter (read host hardware), storage drivers (csi-driver-nfs and Rook Ceph: host network, `hostPath` volumes, bidirectional mount propagation), the Envoy AI Gateway and agentgateway controllers. |
| `baseline` | Upstream operators and controllers whose charts do not declare a full restricted security context: the Keycloak operator, the KServe and LeaderWorkerSet controllers, Velero, and the vLLM Semantic Router dashboard. `baseline` still blocks privilege escalation and host access. |
| `restricted` | Workloads that declare a full security context (non-root, all capabilities dropped, a seccomp profile): cert-manager, the PostgreSQL operator and database, Envoy Gateway (its proxies listen on unprivileged ports), metrics-server, Headlamp, KEDA, the model namespaces, LiteLLM and Langfuse. |

## Namespaces kept out of the mesh

| Namespace | Reason |
|---|---|
| `metallb-system` | The speaker answers ARP on the host network; it is not a mesh service. |
| `node-feature-discovery` | Per-node agents that only label nodes; they serve no mesh traffic. |
| `csi-driver-nfs`, `rook-ceph` | Storage drivers that run on the host network. |
| `vllm-semantic-router-system` | The role sets no Istio label on it. |
| `system` (eRAG) | Internal pipeline orchestration; no east-west mTLS needed between its co-located parts. |
| `nri-balloons-controller` (eRAG) | Host-level CPU pinning daemon; not a mesh service. |

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See where these namespaces sit in the overall layer architecture | [Architecture & Design Document](architecture.md) |
| Understand how Istio ambient mode routes traffic between labeled namespaces | [Network Architecture — Layer 4: Istio Ambient](../deploy/networking.md#layer-4-istio-ambient--service-mesh-mtls) |
| Turn components (and so their namespaces) on or off | [Configuration Reference](../customize/configuration.md) |
