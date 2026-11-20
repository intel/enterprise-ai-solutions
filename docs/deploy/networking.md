# Network Architecture — External Access & Request Flow

[← Docs Index](../README.md)

How a request from outside the cluster reaches a model, which IP address the cluster answers on, and how to make it reachable from your network.

---

## Overview

Every request enters the cluster through one IP address and one gateway. In the default mode (`auth_type: jwt`, see [API authentication](../customize/configuration.md#api-authentication)) the path is:

```
Client (curl, SDK, browser)
    │  HTTPS to inference.<base_domain_name>
    ▼
Your network: DNS, and NAT/firewall if the client is outside the node subnet
    │
    ▼
MetalLB IP (the gateway's LoadBalancer address)
    │
    ▼
Envoy Gateway "eg-gateway": TLS termination, Keycloak JWT check, host/path routing
    │
    ▼
AI gateway "ai-gateway": picks the model from the "model" field of the request
    │
    ▼
Model pod (vLLM or OpenVINO™ Model Server, run by KServe)
```

In LiteLLM mode (`auth_type: litellm_vkey`) clients call `litellm.<base_domain_name>` instead. Envoy Gateway forwards to LiteLLM, which checks the virtual key and then calls the AI gateway. The rest of the path is the same.

The layers below follow that path, one component at a time.

### Service addresses

`routing_mode` in `global_config.yaml` decides how services are told apart:

| Service | `routing_mode: subdomain` (default) | `routing_mode: path` |
|---|---|---|
| Inference API (`jwt` mode) | `https://inference.<domain>/v1/...` | `https://<domain>/inference/v1/...` |
| LiteLLM API (`litellm_vkey` mode) | `https://litellm.<domain>/v1/...` | `https://<domain>/litellm/v1/...` |
| Keycloak | `https://keycloak.<domain>` | `https://<domain>/keycloak` |
| Grafana | `https://grafana.<domain>` | `https://<domain>/grafana` |
| Headlamp (when enabled) | `https://headlamp.<domain>` | `https://<domain>/headlamp` |

`<domain>` is `base_domain_name` (default `inteltoolkits.ai`). Plain HTTP on port 80 only redirects to HTTPS.

---

## Layer 1: MetalLB — LoadBalancer IP Assignment

On a cloud, the provider hands out an external IP for a Kubernetes Service of type `LoadBalancer`. Bare-metal clusters have no such provider. [MetalLB](../glossary.md) fills that gap: it gives the gateway its external IP and makes that IP reachable on your local network.

### Components

```
┌─────────────────────────────────────────────────────┐
│ MetalLB (metallb-system namespace)                  │
├─────────────────────────────────────────────────────┤
│                                                     │
│  Controller (Deployment)                            │
│  ├─ Watches Services of type LoadBalancer           │
│  ├─ Gives each one an IP from the IPAddressPool     │
│  └─ Writes it to Service.status.loadBalancer        │
│                                                     │
│  Speaker (DaemonSet, one pod per node)              │
│  ├─ Answers "who has <IP>?" (ARP) for that IP       │
│  └─ So the local network sends packets to its node  │
│                                                     │
└─────────────────────────────────────────────────────┘
```

The installer creates one pool, `default-pool`, and announces it in Layer 2 mode (an `L2Advertisement`).

### IP Allocation Strategy

With `metallb_ip_range` unset (the default), the installer picks the pool from the cluster's shape:

| Cluster | Pool | Example | Why |
|---|---|---|---|
| **Single node** | The node IP `/32` | `10.1.1.1/32` | Reachable as it is, no extra routing |
| **One control-plane node + workers**, kube-proxy nftables or iptables | The control-plane node IP `/32` | `10.1.1.1/32` | These kube-proxy modes put no LoadBalancer IP on the nodes |
| **More than one control-plane node** | `.240`–`.250` in the first node's `/24` | `10.1.1.240-10.1.1.250` | MetalLB can announce the IP from any of them: if it were one node's own IP, two nodes would answer ARP for it |
| **kube-proxy ipvs**, more than one node | `.240`–`.250` in the first node's `/24` | `10.1.1.240-10.1.1.250` | IPVS binds every LoadBalancer IP on every node (`kube-ipvs0`), so a node IP in the pool would be taken over |

The `.240`–`.250` addresses must be free on that subnet, and clients outside it may need a route or NAT at the edge. To choose the pool yourself, set `metallb_ip_range` in `global_config.yaml` or `nodes.yaml`, for example `"10.1.2.1-10.1.2.14"` or `"203.0.113.50/32"`. Rack-scale installs must set it, to free management-subnet addresses: see [Rack-scale deployment](../rack-scale-deployment.md).

If the cluster already has a working LoadBalancer controller (for example on a BYO cluster), the installer skips MetalLB. Set `metallb_enabled: false` to skip it yourself.

### L2 Mode Operation

1. The **controller** gives the gateway's Service an IP from the pool.
2. One **speaker** becomes the owner of that IP. Envoy Gateway's Service only accepts traffic on nodes that run an Envoy proxy (control-plane nodes, see below), so the owner is a control-plane node.
3. The owner answers ARP requests for the IP with its own MAC address.
4. Packets for the IP arrive at that node, and kube-proxy (nftables by default) hands them to the local Envoy proxy.

Good to know:
- No router configuration is needed; it works on any flat (Layer 2) network.
- One node receives all traffic for the IP at a time.
- If that node fails, another speaker takes the IP over, usually within seconds.

---

## Layer 2: Envoy Gateway — Ingress & Routing

[Envoy Gateway](../glossary.md) is the single entry point. It implements the Kubernetes **Gateway API** and:

- terminates TLS: `gateway_tls_mode: selfsigned` (default, an internal CA) or `custom` (your certificate files);
- checks a Keycloak JWT on every request, unless the route has its own policy (deny by default);
- routes by hostname (subdomain mode) or by path (path mode) to the service behind it;
- applies a request timeout of `gateway_request_timeout` (default `600s`), long enough for slow model answers.

In selfsigned mode the CA certificate is saved as `env/<env>/logs/ai-solutions-ca.crt`. Give it to clients (`curl --cacert ...`) instead of turning certificate checks off.

### How Envoy Gateway gets its IP

Envoy Gateway runs its proxy as a **DaemonSet on every control-plane node** and puts a Service of type `LoadBalancer` in front of it. MetalLB gives that Service its IP. To see it:

```bash
kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway
# EXTERNAL-IP is the address clients connect to
```

On OpenShift there is no MetalLB: the OpenShift router owns ports 80/443 and forwards to Envoy, which then runs as a normal Deployment. See [Deploy on OpenShift](openshift.md).

### Gateway resource

The installer creates one Gateway, `eg-gateway` in `envoy-gateway-system` (simplified):

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: eg-gateway
  namespace: envoy-gateway-system
spec:
  gatewayClassName: eg            # "enterprise-ai" if another controller already owns "eg"
  listeners:
    - name: http                  # port 80: only the redirect to HTTPS
      protocol: HTTP
      port: 80
    - name: https                 # "*.<domain>": routes not on a ListenerSet
      protocol: HTTPS
      port: 443
      hostname: "*.inteltoolkits.ai"
      tls:
        mode: Terminate
        certificateRefs:
          - name: gateway-wildcard-tls   # gateway-tls in custom mode
    - name: https-apex            # the bare domain; every service in path mode
      protocol: HTTPS
      port: 443
      hostname: "inteltoolkits.ai"
      tls:
        mode: Terminate
        certificateRefs:
          - name: gateway-tls
```

### How services attach to the gateway

Each service publishes its own host. How it does so depends on the Envoy Gateway version:

- **Envoy Gateway 1.9 or newer** (`envoy_gateway_upgrade: true` installs 1.9.3): each service adds a **ListenerSet** in its own namespace, with a listener and certificate for its hostname. The Gateway accepts ListenerSets only from namespaces labelled `gateway.enterprise-ai/allow-listenersets: "true"`. An admission policy accepts a hostname only if it is listed in that namespace's `gateway.enterprise-ai/hostnames` annotation.
- **Envoy Gateway 1.7.5** (the default) has no ListenerSets. Services attach their routes to the shared `*.<domain>` listener, and the same hostname grants decide which namespace may serve which host.
- **Path mode**: every service lives on the apex host. Only namespaces labelled `gateway.enterprise-ai/allow-apex-routes: "true"` may attach there, and only under the paths granted to them in the `gateway-path-grants` ConfigMap.

Grants live on namespaces or in the gateway namespace, so only cluster operators can hand out hostnames and paths.

### SecurityPolicy (JWT authentication)

One gateway-wide policy, `gateway-auth`, requires a Keycloak token on every request:

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: gateway-auth
  namespace: envoy-gateway-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: eg-gateway
  jwt:
    providers:
      - name: keycloak
        issuer: https://keycloak.inteltoolkits.ai/realms/inference   # path mode: https://<domain>/keycloak/realms/inference
        audiences:
          - inference-client
        remoteJWKS:
          # fetched inside the cluster, not through the gateway
          uri: http://keycloak-service.keycloak.svc.cluster.local:8080/realms/inference/protocol/openid-connect/certs
```

Routes that sign users in or check their own credential (UI logins, the LiteLLM API) attach their own SecurityPolicy, which replaces this one for that route. With `keycloak_enabled: false` the policy denies every request instead.

---

## Layer 3: Model Serving — AI Gateway and KServe

The inference layer (from the inference repo) adds a second, internal gateway and the model pods.

In `jwt` mode:

1. The inference layer publishes `inference.<domain>` and creates the HTTPRoute `inference-external` in `envoy-gateway-system`. It sends every request for that host to the AI gateway's Service on port 80.
2. The **AI gateway** (`ai-gateway`, Envoy AI Gateway, internal only) reads the `"model"` field from the JSON body and picks that model's backend.
3. **KServe** runs each model as its own workload. For an `LLMInferenceService` the backend is an `InferencePool`: a scheduler picks the least-loaded replica. Other models are reached through their Service on port 8000.

In `litellm_vkey` mode `inference.<domain>` is not published at all (requests there get `404`). LiteLLM calls the AI gateway over the cluster network instead.

For every resource name and the full detail, see the inference repo's request flow guide (after `init`: `ext/enterprise.ai-inference/docs/reference/request_flow.md`) and its [Accessing Models](../../ext/enterprise.ai-inference/docs/deploy/accessing_models.md) guide.

---

## Layer 4: Istio Ambient — Service Mesh (mTLS)

Workload namespaces are enrolled in **Istio ambient mode** (`istio_enabled: true`, the default). Traffic between pods is encrypted and authenticated with mutual TLS (mTLS) by the `ztunnel` DaemonSet. No sidecars are injected, and applications don't change.

- **Pod-to-pod traffic (L4):** ztunnel adds mTLS automatically.
- **Enforcement is STRICT:** a mesh-wide `PeerAuthentication` named `default` in `istio-system` (`mtls.mode: STRICT`) makes ztunnel reject any plaintext traffic into a mesh workload. Set it with `istio_mtls_mode`; the platform `istio` role applies it.
- **Each component owns its exceptions:** the role that creates a workload also applies its `PeerAuthentication`, right after enrolling its namespace. The `istio` role owns only the mesh-wide default and the `cert-manager` exception. So nothing reserves a namespace or port for a component that isn't installed.
- **Mesh-edge exceptions:** workloads that receive traffic from *outside* the mesh get `PeerAuthentication: PERMISSIVE`, for them only:
  - `cert-manager` (whole namespace, **istio role**): the trust root (istio-csr CA and the cert-manager webhook). STRICT here would block certificate bootstrap.
  - `eg-gateway` (its pods only, **envoy_gateway role**): the edge proxy. External clients have no mesh identity. The internal `ai-gateway` proxy in the same namespace stays STRICT.
- **Admission-webhook exceptions:** the Kubernetes API server runs on the host network and has **no mesh identity**, so STRICT would reject its calls to admission webhooks inside the mesh (symptom: `failed calling webhook … EOF`). Each operator's role opens **only** its webhook port as PERMISSIVE (`9443` for the CNPG, KServe, LWS, AI gateway and Envoy Gateway controllers; `10250`/`6443` for the Prometheus operator and adapter; `10250` for metrics-server). Where nothing else uses that port, an `AuthorizationPolicy` lets only the node addresses reach it. Every other port stays STRICT.
- **Calico** routes pod IPs between nodes; Istio adds identity and encryption on top.
- **Not in the mesh:** `metallb-system` (raw ARP), `csi-driver-nfs` (host network; it mounts NFS on the node), `kube-system`.

The mesh doesn't change the request path described here. It adds a small encryption cost per hop.

---

## Complete Request Flow

Every hop of one inference request in the default `jwt` mode, on a cluster with one control-plane node (`10.1.1.1`) and two workers:

```
┌────────────────────────────────────────────────────────────────────────┐
│ [1] Client                                                             │
│     POST https://inference.inteltoolkits.ai/v1/chat/completions        │
│     Authorization: Bearer <Keycloak JWT>                               │
│     Body: {"model": "qwen3-0-6b", "messages": [...]}                   │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [2] DNS: inference.inteltoolkits.ai → the gateway's MetalLB IP         │
│     (or a public IP that your firewall NATs to it)                     │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [3] MetalLB: the control-plane node answers ARP for 10.1.1.1           │
│     kube-proxy hands the packet to the Envoy proxy on that node        │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [4] Envoy Gateway (eg-gateway proxy, on the control-plane node)        │
│     ├─ TLS terminated with the gateway certificate                     │
│     ├─ JWT checked against Keycloak (realm inference)                  │
│     │    missing / invalid / expired → 401, request stops here         │
│     └─ Host inference.inteltoolkits.ai → HTTPRoute inference-external  │
│          → AI gateway Service, port 80                                 │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [5] AI gateway (ai-gateway proxy)                                      │
│     ├─ Reads "model": "qwen3-0-6b" from the body                       │
│     └─ Asks the model's scheduler (InferencePool) for the best replica │
└────────────────────────────────────────────────────────────────────────┘
                              ↓  pod network (Calico), mTLS by Istio
┌────────────────────────────────────────────────────────────────────────┐
│ [6] Model pod (llm-inference namespace, on a worker node)              │
│     vLLM or OpenVINO Model Server computes the answer on port 8000     │
│     ← nearly all of the request's time is spent here                   │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
          The response goes back the same way to the client
```

The network hops add a few milliseconds at most, mostly for TLS and the JWT check. Generating the answer takes from tens of milliseconds to many seconds, depending on the model and prompt.

---

## Multi-Node Network Topology

### Example: 1 control-plane node + 2 workers

```
                         Clients
                         │  DNS: *.inteltoolkits.ai → 10.1.1.1
                         │  (or a public IP NATed to it)
                         ▼
          Node network 10.1.1.0/24
       ┌─────────────────┼─────────────────┐
       │                 │                 │
 ┌─────▼──────┐    ┌─────▼──────┐    ┌─────▼──────┐
 │ Control    │    │ Worker1    │    │ Worker2    │
 │ plane      │    │ 10.1.1.2   │    │ 10.1.1.3   │
 │ 10.1.1.1   │    │            │    │            │
 │            │    │ model pods │    │ model pods │
 │ MetalLB IP │    └─────┬──────┘    └─────┬──────┘
 │ Envoy proxy│          │                 │
 │ Keycloak,  │          │                 │
 │ PostgreSQL │          │                 │
 └─────┬──────┘          │                 │
       └─────────────────┴─────────────────┘
       Pod network (Calico): 10.233.64.0/18
       Services (ClusterIP): 10.233.0.0/18
```

- The MetalLB IP is the control-plane node's own IP here (see [IP Allocation Strategy](#ip-allocation-strategy)).
- The Envoy proxy runs on the control-plane node. Platform pods prefer it and model pods prefer the workers ([Node Topology](../customize/node_topology.md)).
- The pod and Service ranges are the defaults, `kubernetes_cluster_cidr` and `kubernetes_service_cidr`. Change them in `global_config.yaml` if they overlap your network.

### Traffic flow

1. The client connects to `10.1.1.1:443` (directly, or through NAT at your network edge).
2. The packet arrives at the control-plane node, where the Envoy proxy runs.
3. Envoy sends the request to the AI gateway, which picks a model replica, for example on `worker1`.
4. Calico carries the packet from the control-plane node to `worker1`.

---

## Multi-Master HA Topology

With three control-plane nodes, every one of them runs an Envoy proxy, and MetalLB can move the gateway IP between them.

### Example: 3 control-plane nodes + 2 workers

```
                    Clients
                       │  DNS: *.inteltoolkits.ai → 10.1.1.240
                       ▼
             MetalLB IP 10.1.1.240
        (owned by one control-plane node;
         another takes over if it fails)
                       │
   ┌───────────────────┼───────────────────┐
   │                   │                   │
┌──▼───────┐     ┌─────▼────┐       ┌──────▼───┐
│ Master1  │     │ Master2  │       │ Master3  │
│ 10.1.1.1 │     │ 10.1.1.2 │       │ 10.1.1.3 │
│ Envoy    │     │ Envoy    │       │ Envoy    │
│ etcd     │     │ etcd     │       │ etcd     │
└──────────┘     └─────┬────┘       └──────────┘
                       │
         ┌─────────────┴─────────────┐
   ┌─────▼─────┐               ┌─────▼─────┐
   │ Worker1   │               │ Worker2   │
   │ 10.1.1.10 │               │ 10.1.1.11 │
   └───────────┘               └───────────┘
```

- With more than one control-plane node, the automatic pool is `10.1.1.240-10.1.1.250`, so the gateway gets `10.1.1.240`. Those addresses must be free; otherwise set `metallb_ip_range`.
- If the node that owns the IP fails, MetalLB moves it to another control-plane node, and its local Envoy proxy keeps serving.
- etcd keeps working while 2 of the 3 control-plane nodes are up.

An external load balancer is not needed. If you put one in front anyway, point it at the MetalLB IP, not at the node IPs.

---

## External Access Configuration

To reach the cluster from outside the node network, give clients a name that resolves to the gateway, and, if needed, forward traffic to it.

### Step 1 — Find the gateway IP

```bash
export KUBECONFIG=$(pwd)/env/<env>/kubeconfig.yaml
kubectl get svc -n envoy-gateway-system \
  -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway \
  -o jsonpath='{.items[0].status.loadBalancer.ingress[0].ip}'
```

### Step 2 — Set up DNS

**Option 1: wildcard record (recommended).** One record covers every subdomain:
```
*.inteltoolkits.ai.  IN  A  203.0.113.50
```
In path mode, add a record for the bare domain too:
```
inteltoolkits.ai.    IN  A  203.0.113.50
```

**Option 2: one record per service:**
```
inference.inteltoolkits.ai.  IN  A  203.0.113.50
keycloak.inteltoolkits.ai.   IN  A  203.0.113.50
grafana.inteltoolkits.ai.    IN  A  203.0.113.50
```

**Option 3: `/etc/hosts` on the client (for testing):**
```bash
203.0.113.50  inference.inteltoolkits.ai keycloak.inteltoolkits.ai grafana.inteltoolkits.ai
```

Use the gateway IP from step 1 if clients are on the node network, or your public IP if they come through NAT.

### Step 3 — Forward traffic (only if clients are outside the node network)

Forward TCP ports 443 and 80 from your public IP to the gateway IP. On a dedicated firewall or router, add a port-forwarding rule. On a Linux host acting as the router:

```bash
# Let the host forward packets
sysctl -w net.ipv4.ip_forward=1

# Send public :443 and :80 to the gateway IP (10.1.1.1 here)
iptables -t nat -A PREROUTING -d 203.0.113.50 -p tcp --dport 443 \
  -j DNAT --to-destination 10.1.1.1:443
iptables -t nat -A PREROUTING -d 203.0.113.50 -p tcp --dport 80 \
  -j DNAT --to-destination 10.1.1.1:80

# Allow the forwarded traffic and the replies
iptables -A FORWARD -d 10.1.1.1 -j ACCEPT
iptables -A FORWARD -s 10.1.1.1 -j ACCEPT
iptables -t nat -A POSTROUTING -s 10.1.1.0/24 -j MASQUERADE

# Keep the rules after a reboot (iptables-persistent)
iptables-save > /etc/iptables/rules.v4
```

Don't run these on a cluster node: Kubernetes manages the firewall rules there.

### Step 4 — Check it works

```bash
curl --cacert env/<env>/logs/ai-solutions-ca.crt https://inference.inteltoolkits.ai/v1/models
# 401 means the gateway answered and asks for a token: networking is working
```

---

## Performance Notes

The defaults already keep network overhead small:

- **A node IP as the MetalLB IP where it is safe** (a single node, or one control-plane node with nftables or iptables), so no extra router hop. See [IP Allocation Strategy](#ip-allocation-strategy).
- **kube-proxy in nftables mode** (the default, `kubernetes_kube_proxy_mode: nftables`): fast rule matching, and unlike IPVS it never binds LoadBalancer IPs to the nodes.
- **NAT on a dedicated firewall or router** rather than on a cluster node.

MetalLB runs in Layer 2 mode only: the installer does not configure BGP peering.

---

## Troubleshooting

Commands for each hop, in the order a request travels.

### 1. Does the gateway have an IP?
```bash
kubectl get svc -n envoy-gateway-system -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway
# EXTERNAL-IP should be an address from the MetalLB pool, not <pending>
kubectl get ipaddresspool -n metallb-system   # the pool the installer created
```

### 2. Does the network see the IP?
```bash
# From another machine on the same subnet
arping -c 3 10.1.1.1
# Replies should come from one node's MAC address
```

### 3. Does the gateway answer?
```bash
curl --cacert env/<env>/logs/ai-solutions-ca.crt \
  --resolve inference.inteltoolkits.ai:443:10.1.1.1 \
  https://inference.inteltoolkits.ai/v1/models
# 401 = gateway reached, token needed. 404 = no route for that host
```

### 4. Is the route there?
```bash
kubectl get httproute -A
kubectl describe httproute inference-external -n envoy-gateway-system
```

### 5. Follow a request through Envoy
```bash
kubectl logs -n envoy-gateway-system -l gateway.envoyproxy.io/owning-gateway-name=eg-gateway -f
# Then, in another terminal, send a request with a token
# (see Accessing Models for how to get $TOKEN)
curl --cacert env/<env>/logs/ai-solutions-ca.crt -H "Authorization: Bearer $TOKEN" \
  https://inference.inteltoolkits.ai/v1/models
```

---

## Summary

1. **One IP, one gateway.** MetalLB gives Envoy Gateway one address; every service is reached through it by hostname or path.
2. **Auth at the edge.** Envoy Gateway checks the Keycloak JWT before a request reaches any model (or LiteLLM checks the key, in LiteLLM mode).
3. **Routing by model name.** The AI gateway picks the model from the request body, then the least-loaded replica.
4. **Network overhead is small.** Almost all request time is model computation.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See how this networking layer fits with platform, inference, and application layers | [Architecture & Design Document](../reference/architecture.md) |
| Set up the multi-node or HA cluster this topology assumes | [Multi-Node & BYO Cluster](topologies.md) |
| Change `gateway_request_timeout`, TLS mode, or other gateway settings | [Configuration Reference](../customize/configuration.md) |
| Route additional tenants, versions, or canaries through the gateway | [Integration Guide — Routing Patterns](../customize/integration.md#routing-patterns-host--path) |
