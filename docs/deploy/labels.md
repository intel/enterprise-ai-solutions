# Namespace Security Labels

[← Docs index](../README.md)

The installer labels every namespace it manages for Pod Security Admission (PSA)
and for Istio ambient mode. This page is a lookup of those labels. Live cluster
state was captured with `kubectl get namespaces` on 2026-06-02. The code source
of truth is each role's `tasks/install.yaml`.

## Legend

| Column | Values |
|---|---|
| **PSA enforce** | `privileged` / `baseline` / `restricted` /, (unlabeled) |
| **PSA audit** | same, violations logged, not blocked |
| **PSA warn** | same, violations surfaced as API warnings |
| **Istio** | `ambient` = enrolled in ztunnel mesh /, = not enrolled |

---

## Platform layer (`applications.ai.enterprise.ai-solutions`)

| Namespace | PSA enforce | PSA audit | PSA warn | Istio | Role |
|---|---|---|---|---|---|
| `istio-system` | `privileged` | `privileged` | `privileged` | `ambient` | `istio` |
| `cert-manager` | `restricted` | `restricted` | `restricted` | `ambient` | `cert_manager` |
| `envoy-gateway-system` | `privileged` | `privileged` | `privileged` | `ambient` | `envoy_gateway` |
| `metallb-system` | `privileged` | `privileged` | `privileged` |, | `metallb` |
| `cnpg-system` | `restricted` | `restricted` | `restricted` | `ambient` | `postgresql` |
| `postgresql` | `restricted` | `restricted` | `restricted` | `ambient` | `postgresql` |
| `keycloak` | `baseline` | `restricted` | `restricted` | `ambient` | `keycloak` |
| `monitoring` | `privileged` | `privileged` | `privileged` | `ambient` | `observability` |
| `minio` | `restricted` | `restricted` | `restricted` | `ambient` | `minio` |
| `nfs-provisioner` | `restricted` | `restricted` | `restricted` |, | `nfs_storage` ² |

² Only created when `storage_backend: nfs`.

---

## Inference layer (`applications.ai.enterprise.ai-inference`)

| Namespace | PSA enforce | PSA audit | PSA warn | Istio | Role |
|---|---|---|---|---|---|
| `envoy-ai-gateway-system` | `privileged` | `privileged` | `privileged` | `ambient` | `envoy_ai_gateway` |
| `kserve` | `baseline` | `restricted` | `restricted` | `ambient` | `kserve` |
| `lws-system` | `baseline` | `restricted` | `restricted` | `ambient` | `kserve` |
| `llm-inference` | `privileged` | `privileged` | `privileged` | `ambient` | `llm_services` |

---

## Application layer (`applications.ai.enterprise.ai-erag`)

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
| `system` | `restricted` | `restricted` | `restricted` |, | `app_pipeline` |
| `erag-gateway` | `privileged` | `privileged` | `privileged` | `ambient` | `app_gmc` |
| `auth-apisix` | `privileged` | `privileged` | `privileged` | `ambient` | `app_apisix` |
| `monitoring-traces` | `privileged` | `privileged` | `privileged` | `ambient` | `app_telemetry` |
| `nri-balloons-controller` | `privileged` | `privileged` | `privileged` |, | `app_nri_balloons` |

¹ `chatqa` is the `pipeline_namespace` value on this cluster, the actual namespace name is driven by `pipeline_type`.

---

## Unlabeled (not managed by installer)

| Namespace | Notes |
|---|---|
| `default` | Not used by workloads |
| `kube-system` | Kubernetes internals, PSA disabled on this ns by default |
| `kube-public` / `kube-node-lease` | Kubernetes internals |

---

## PSA profile rationale

| Profile | Used for |
|---|---|
| `privileged` | Workloads requiring `seccompProfile: Unconfined`, `NET_ADMIN`/`NET_RAW`, host PID/network, or root, CPU inference (vLLM), L2 speakers (MetalLB), service mesh + gateway data-planes (Envoy, APISIX, istio-cni/ztunnel), observability agents (node-exporter), object storage (SeaweedFS). |
| `baseline` | Upstream operators/controllers that don't fully declare securityContext, blocks privilege escalation without rejecting third-party images. Applies to: Keycloak operator, KServe controller, LWS controller. |
| `restricted` | Cloud-native workloads that declare full securityContext (non-root, caps.drop=ALL, seccompProfile). Applies to all application workloads and data-tier (CNPG, PostgreSQL, cert-manager). |

## Istio ambient exclusions

| Namespace | Reason |
|---|---|
| `metallb-system` | MetalLB L2 speaker uses raw ARP sockets at host level, not a mesh participant. |
| `system` (GMC) | Internal pipeline orchestration; east-west mTLS not required between co-located components. |
| `nri-balloons-controller` | Host-level CPU pinning daemon; not a service mesh participant. |

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See where these namespaces sit in the overall layer architecture | [Architecture & Design](../meet/architecture.md) |
| Understand how Istio ambient mode routes traffic between labeled namespaces | [Network Architecture, Layer 4: Istio Ambient](networking.md#layer-4-istio-ambient--service-mesh-mtls) |
| Change which namespaces get created during install | [Configuration Reference](../customize/configuration.md) |
