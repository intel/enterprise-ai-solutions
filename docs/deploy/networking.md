# Network Architecture — External Access & Request Flow

[← Docs Index](../README.md)

Complete guide to network topology, IP allocation, and request routing in the Intel® AI for Enterprise Solutions stack.

---

## Overview

Intel® AI for Enterprise Solutions provides **external access to AI inference endpoints** through a multi-layer architecture:

```
External Client
    ↓ (Internet/VPN)
Firewall/NAT (port forwarding)
    ↓ (Layer 3 routing)
MetalLB (Layer 2 ARP announcement)
    ↓ (LoadBalancer Service)
Envoy Gateway (TLS termination + JWT auth + routing)
    ↓ (HTTPRoute matching)
KServe Predictor Pod (vLLM inference)
```

---

## Layer 1: MetalLB — LoadBalancer IP Assignment

MetalLB is the component that gives Intel® AI for Enterprise Solutions LoadBalancer IPs on bare-metal, on-premises clusters that have no cloud load balancer to call.

### Purpose

Provides **LoadBalancer Service** support on bare-metal Kubernetes (equivalent to AWS ELB, GCP Load Balancer).

### Components

```
┌─────────────────────────────────────────────────────┐
│ MetalLB (metallb-system namespace)                  │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Controller (Deployment)                            │
│  ├─ Watches Service type=LoadBalancer               │
│  ├─ Allocates IP from IPAddressPool                 │
│  └─ Updates Service.status.loadBalancer.ingress     │
│                                                      │
│  Speaker (DaemonSet)                                │
│  ├─ Runs on ALL nodes                               │
│  ├─ Announces IP via ARP (Layer 2 mode)             │
│  ├─ Responds to "who has <IP>?" queries             │
│  └─ Makes IP routable on local network              │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### IP Allocation Strategy

The installer automatically selects optimal IPs based on cluster topology:

| Cluster Type | MetalLB IP Pool | Example | Rationale |
|--------------|----------------|---------|-----------|
| **Single-node** | Node IP `/32` | `10.1.1.1/32` | Direct routing, zero extra hops |
| **Multi-node (1 master + workers)** | First master IP `/32` | `10.1.1.1/32` | Master is stable, already routes traffic |
| **Multi-master HA (2-3 masters)** | Master IP range | `10.1.1.1-10.1.1.3` | Enables HA with external LB failover |

**Why not `.240-.250` range?**

❌ **Old approach** (avoided):
```yaml
metallb_ip_range: "10.1.1.240-10.1.1.250"  # BAD
```
- Creates "virtual IPs" that don't exist on any physical interface
- Requires additional routing configuration
- Not reachable from external networks without complex setup
- Adds unnecessary network hops

✅ **New approach** (default):
```yaml
metallb_ip_range: "10.1.1.1/32"  # GOOD (master node IP)
```
- Uses real node IP that's already routable
- Zero configuration needed for NAT/port-forwarding
- Direct path from external → node interface
- Production-grade pattern

### L2 Mode Operation

MetalLB operates in **Layer 2 mode** by default:

1. **Controller** allocates IP from pool → Service gets external IP
2. **Leader election** — One Speaker pod becomes leader for that IP
3. **ARP announcement** — Leader responds to ARP requests with node's MAC address
4. **Traffic ingress** — Packets arrive at the elected node's interface
5. **Kubernetes routing** — kube-proxy/IPVS forwards to Service ClusterIP

**Characteristics:**
- ✅ Simple setup (no BGP configuration needed)
- ✅ Works on any L2 network
- ⚠️ Single node handles all traffic (leader election)
- ⚠️ On node failure, new leader elected (~10 seconds failover)

---

## Layer 2: Envoy Gateway — Ingress & Routing

Envoy Gateway is the ingress and auth-enforcement point for every request Intel® AI for Enterprise Solutions serves.

### Purpose

Kubernetes **Gateway API** implementation providing:
- TLS termination (self-signed, Let's Encrypt, or custom certs)
- JWT authentication via Keycloak
- HTTP routing to backend services
- Load balancing across pods

### How Envoy Gateway Gets Its IP

```yaml
# Created automatically by Envoy Gateway
apiVersion: v1
kind: Service
metadata:
  name: envoy-eg-gateway-<hash>
  namespace: envoy-gateway-system
  labels:
    gateway.envoyproxy.io/owning-gateway-name: eg-gateway
spec:
  type: LoadBalancer  # ← Triggers MetalLB IP allocation
  ports:
    - name: http
      port: 80
      targetPort: 8080
    - name: https
      port: 443
      targetPort: 8443
  selector:
    gateway.envoyproxy.io/owning-gateway-name: eg-gateway
```

**Sequence:**
1. Envoy Gateway controller creates LoadBalancer Service
2. MetalLB controller sees the Service, allocates IP from pool
3. MetalLB Speaker announces IP via ARP on local network
4. Service status updated: `status.loadBalancer.ingress[0].ip: 10.1.1.1`

### Gateway Resource

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: eg-gateway
  namespace: envoy-gateway-system
spec:
  gatewayClassName: eg
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      allowedRoutes:
        namespaces:
          from: All
    - name: https
      protocol: HTTPS
      port: 443
      hostname: "*.inference-example.com"
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: gateway-tls
      allowedRoutes:
        namespaces:
          from: All
```

### SecurityPolicy (JWT Authentication)

```yaml
apiVersion: gateway.envoyproxy.io/v1alpha1
kind: SecurityPolicy
metadata:
  name: jwt-auth
  namespace: envoy-gateway-system
spec:
  targetRef:
    group: gateway.networking.k8s.io
    kind: Gateway
    name: eg-gateway
  jwt:
    providers:
      - name: keycloak
        issuer: https://keycloak.inference-example.com/realms/inference
        audiences:
          - inference-client
        remoteJWKS:
          uri: https://keycloak.inference-example.com/realms/inference/protocol/openid-connect/certs
```

---

## Layer 3: KServe — Model Serving

KServe is the model-serving layer Intel® AI for Enterprise Solutions uses to run vLLM and OpenVINO™ Model Server workloads on Kubernetes.

### HTTPRoute Auto-Creation

When you deploy an `LLMInferenceService`, KServe **automatically creates** an HTTPRoute:

```yaml
# Auto-created by KServe controller
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: llama-3-2-1b
  namespace: llm-inference
  ownerReferences:
    - apiVersion: serving.kserve.io/v1alpha1
      kind: LLMInferenceService
      name: llama-3-2-1b
spec:
  parentRefs:
    - name: eg-gateway
      namespace: envoy-gateway-system
  hostnames:
    - llama-3-2-1b.inference-example.com
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /
      backendRefs:
        - name: llama-3-2-1b-predictor
          port: 8000
```

### Predictor Service

```yaml
apiVersion: v1
kind: Service
metadata:
  name: llama-3-2-1b-predictor
  namespace: llm-inference
spec:
  type: ClusterIP
  selector:
    serving.kserve.io/inferenceservice: llama-3-2-1b
  ports:
    - port: 8000
      targetPort: 8000
```

---

## Layer 4: Istio Ambient — Service Mesh (mTLS)

In Intel® AI for Enterprise Solutions, all workload namespaces are enrolled in **Istio ambient mode**. This provides transparent mutual TLS (mTLS) between pods via the `ztunnel` DaemonSet — no sidecars injected.

- **L4 (ztunnel)**: Automatic mTLS for all east-west pod traffic. Zero config, zero application changes.
- **Enforcement is STRICT**: a mesh-wide `PeerAuthentication` named `default` in `istio-system` (`mtls.mode: STRICT`) makes ztunnel **reject any plaintext inbound** to a mesh workload. Configurable via `istio_mtls_mode`; applied by the core `istio` role.
- **Exceptions are owned by the component that creates the workload**, not centralized in the istio role: each role applies its own `PeerAuthentication` right after its ambient-label task. The istio role owns only the mesh-wide default and the `cert-manager` exception (istio-csr is its own dependency). This way nothing pre-defines a namespace/port for a component that may not be installed.
- **Mesh-edge exceptions** — namespaces/workloads that receive traffic from *outside* the mesh get a `PeerAuthentication: PERMISSIVE` overriding STRICT for them only:
  - `cert-manager` (namespace-wide, **istio role**) — the trust root (istio-csr CA + cert-manager webhook); STRICT here deadlocks cert bootstrap.
  - `eg-gateway` (selector-scoped, **envoy_gateway role**) — the north-south ingress edge proxy; external clients present no mesh identity. The internal `ai-gateway` proxy in the same namespace stays STRICT (reached in-mesh via passthrough).
- **Admission-webhook exceptions** — the kube-apiserver is host-network with **no mesh identity**, so STRICT rejects its calls to any in-mesh admission webhook (symptom: `failed calling webhook … EOF` at admission time). Each owning role gives its operator a `PeerAuthentication` with `portLevelMtls` opening **only** the webhook container port (`9443` for CNPG/KServe/LWS/AI-gateway/envoy-gateway controllers; `10250`/`6443` for the Prometheus operator/adapter) as PERMISSIVE — every other port (e.g. metrics scrape) stays STRICT.
- **Calico handles L3 routing** (pod-to-pod IP, VXLAN or direct), Istio handles **L4 identity and encryption** on top.
- Namespaces excluded from the mesh: `metallb-system` (raw ARP), `kube-system`.

The ambient mesh is transparent to the request flow described below — it adds ~0.1-0.3ms per hop for encryption/decryption but does not change the routing topology.

---

## Complete Request Flow

Here is every hop an inference request takes through Intel® AI for Enterprise Solutions, from the external client to the vLLM predictor pod and back.

### Scenario: Client Requests Inference

```
┌────────────────────────────────────────────────────────────────────────┐
│ [1] External Client (e.g., curl from laptop)                           │
│     HTTPS POST https://llama-3-2-1b.inference-example.com/v1/completions│
│     Authorization: Bearer eyJhbGc...                                    │
│     Body: {"prompt": "Hello", "max_tokens": 100}                       │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [2] DNS Resolution                                                      │
│     llama-3-2-1b.inference-example.com → 203.0.113.50 (public IP)     │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [3] Firewall/NAT (network edge)                                        │
│     Port forwarding: 203.0.113.50:443 → 10.1.1.1:443                  │
│     Latency: ~0.01ms (hardware) to ~1ms (software NAT)                │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [4] Network Layer 2 — ARP Resolution                                   │
│     Switch: "Who has 10.1.1.1?"                                        │
│     MetalLB Speaker on master: "I do! MAC: aa:bb:cc:..."               │
│     Packet delivered to master node's network interface                │
│     Latency: <0.001ms (hardware switching)                             │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [5] Master Node (10.1.1.1) — Kernel Routing                            │
│     iptables/IPVS: 10.1.1.1:443 → ClusterIP 10.96.10.50:443           │
│     Service: envoy-eg-gateway-<hash>                                   │
│     kube-proxy load-balances to Envoy Gateway pod                      │
│     (Pod could be on master or any worker node)                        │
│     Latency: <0.1ms (iptables) or <0.01ms (IPVS)                      │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [6] Envoy Gateway Pod (e.g., running on worker2: 10.1.1.3)            │
│                                                                         │
│     ┌─────────────────────────────────────────────┐                   │
│     │ TLS Termination                             │                   │
│     │ - Decrypt HTTPS using gateway-tls secret    │                   │
│     │ Latency: ~1-5ms (RSA/ECDSA handshake)       │                   │
│     └─────────────────────────────────────────────┘                   │
│                         ↓                                               │
│     ┌─────────────────────────────────────────────┐                   │
│     │ JWT Validation (SecurityPolicy)             │                   │
│     │ - Extract Bearer token from header          │                   │
│     │ - Verify signature with Keycloak JWKS       │                   │
│     │ - Check issuer, audience, expiration        │                   │
│     │ Latency: ~1-5ms (JWKS cached after first)   │                   │
│     └─────────────────────────────────────────────┘                   │
│                         ↓                                               │
│     ┌─────────────────────────────────────────────┐                   │
│     │ HTTPRoute Matching                          │                   │
│     │ - Host: llama-3-2-1b.inference-example.com  │                   │
│     │ - Path: /v1/completions                     │                   │
│     │ → Match found: route to llm-inference/      │                   │
│     │   llama-3-2-1b-predictor:8000               │                   │
│     │ Latency: <0.1ms (in-memory routing table)   │                   │
│     └─────────────────────────────────────────────┘                   │
│                         ↓                                               │
│     ┌─────────────────────────────────────────────┐                   │
│     │ Load Balance to Backend                     │                   │
│     │ - Query available pods via Kubernetes API   │                   │
│     │ - Select healthy replica (round-robin)      │                   │
│     │ Latency: <0.5ms                             │                   │
│     └─────────────────────────────────────────────┘                   │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [7] Pod Network — Calico CNI                                           │
│     Envoy pod (worker2: 10.1.1.3)                                      │
│       → vLLM pod (worker1: 10.1.1.2)                                   │
│     Calico direct routing (if same subnet) OR VXLAN overlay            │
│     Latency: ~0.1ms (direct) or ~0.5ms (VXLAN)                        │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
┌────────────────────────────────────────────────────────────────────────┐
│ [8] vLLM Predictor Pod (llm-inference namespace)                       │
│     Name: llama-3-2-1b-predictor-<hash>                                │
│     Node: worker1 (10.1.1.2)                                           │
│                                                                         │
│     ┌─────────────────────────────────────────────┐                   │
│     │ vLLM Engine                                 │                   │
│     │ - Model: Llama-3.2-1B-Instruct              │                   │
│     │ - CPU: 8 cores (pinned by cpumanager)       │                   │
│     │ - Memory: 24Gi                              │                   │
│     │ - Process inference request                 │                   │
│     │ Latency: ~50-500ms (model-dependent)        │                   │
│     │ ← THIS IS 99% OF TOTAL LATENCY ←            │                   │
│     └─────────────────────────────────────────────┘                   │
│                         ↓                                               │
│     Response: {"choices": [...], "usage": {...}}                       │
└────────────────────────────────────────────────────────────────────────┘
                              ↓
        [Response path: reverse through all layers]
                              ↓
                    Client receives inference result
```

### Latency Breakdown

| Layer | Operation | Typical Latency | % of Total |
|-------|-----------|----------------|------------|
| **NAT/Firewall** | Port forwarding | 0.01 - 1ms | <0.2% |
| **MetalLB** | ARP + L2 routing | <0.001ms | <0.001% |
| **kube-proxy** | iptables/IPVS | 0.01 - 0.1ms | <0.02% |
| **Envoy Gateway** | TLS + JWT + routing | 2 - 10ms | 1-2% |
| **Calico CNI** | Pod-to-pod network | 0.1 - 0.5ms | <0.1% |
| **vLLM Inference** | Model computation | 50 - 500ms | **98%** |

**Total network overhead: ~2-12ms** (negligible compared to inference time)

---

## Multi-Node Network Topology

A typical multi-node Intel® AI for Enterprise Solutions deployment on-premises looks like this at the network layer.

### Example: 1 Master + 2 Workers

```
                    External Network (Internet)
                              │
                              │ DNS: *.inference-example.com
                              │      → 203.0.113.50
                              ▼
                    ┌───────────────────┐
                    │ Firewall/Router   │
                    │ Public: 203.0.113.50│
                    │ NAT: 443→10.1.1.1:443│
                    └───────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            │   Private Network (10.1.1.0/24)   │
            └─────────────────┼─────────────────┘
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
      ┌─────▼────┐      ┌─────▼────┐      ┌────▼─────┐
      │ Master   │      │ Worker1  │      │ Worker2  │
      │10.1.1.1  │      │10.1.1.2  │      │10.1.1.3  │
      └──────────┘      └──────────┘      └──────────┘
            │                 │                 │
            │    MetalLB IP: 10.1.1.1 (master)  │
            │    Speaker elects master as owner │
            │                 │                 │
            └─────────────────┼─────────────────┘
                              │
                    Kubernetes Services
                    (ClusterIP: 10.96.0.0/12)
                              │
            ┌─────────────────┼─────────────────┐
            │                 │                 │
    ┌───────▼──────┐  ┌───────▼──────┐  ┌──────▼───────┐
    │ Envoy Pod    │  │ vLLM Pod     │  │ Keycloak Pod │
    │ (any node)   │  │ (worker1)    │  │ (master)     │
    └──────────────┘  └──────────────┘  └──────────────┘
            │                 │                 │
            └─────────────────┼─────────────────┘
                              │
                      Pod Network (Calico)
                      (10.244.0.0/16 VXLAN)
```

### Traffic Flow Detail

**External request → Master:**
1. Client sends to `203.0.113.50:443`
2. Firewall NATs to `10.1.1.1:443`
3. Arrives at master's `eth0` interface

**Master → Envoy Gateway pod (could be on worker2):**
1. Master kernel: iptables DNAT `10.1.1.1:443` → ClusterIP `10.96.10.50:443`
2. kube-proxy load-balances to Envoy pod IP `10.244.2.5` (on worker2)
3. Calico routes: master (`10.1.1.1`) → worker2 (`10.1.1.3`) via VXLAN
4. Packet arrives at Envoy pod

**Envoy pod → vLLM pod (on worker1):**
1. Envoy resolves backend: `llama-3-2-1b-predictor.llm-inference.svc:8000`
2. DNS returns ClusterIP: `10.96.20.100`
3. kube-proxy load-balances to vLLM pod IP `10.244.1.10` (on worker1)
4. Calico routes: worker2 (`10.1.1.3`) → worker1 (`10.1.1.2`) direct or VXLAN

**Total hops:**
- External → Master: **1 hop** (NAT at edge)
- Master → Envoy pod: **1 hop** (Calico CNI)
- Envoy pod → vLLM pod: **1 hop** (Calico CNI)
- **Total: 3 hops**, each <1ms

---

## Multi-Master HA Topology

For production, high-availability Intel® AI for Enterprise Solutions clusters, MetalLB fails over across multiple control-plane nodes instead of relying on a single master.

### Example: 3 Masters + 2 Workers

```
                    External Network
                              │
                    ┌─────────▼────────┐
                    │  External LB     │
                    │  (HAProxy/nginx) │
                    │  203.0.113.50    │
                    └──────────────────┘
                              │
            Round-robin across masters:
            ├─→ 10.1.1.1:443 (master1)
            ├─→ 10.1.1.2:443 (master2)
            └─→ 10.1.1.3:443 (master3)
                              │
            ┌─────────────────┼─────────────────┐
            │   Private Network (10.1.1.0/24)   │
            └─────────────────┼─────────────────┘
                              │
      ┌───────────┬───────────┼───────────┬───────────┐
      │           │           │           │           │
 ┌────▼───┐ ┌────▼───┐ ┌────▼───┐ ┌─────▼──┐ ┌─────▼──┐
 │Master1 │ │Master2 │ │Master3 │ │Worker1 │ │Worker2 │
 │10.1.1.1│ │10.1.1.2│ │10.1.1.3│ │10.1.1.10│ │10.1.1.11│
 └────────┘ └────────┘ └────────┘ └────────┘ └─────────┘
      │           │           │           │           │
      │  MetalLB IP range: 10.1.1.1-10.1.1.3         │
      │  Speaker can elect any master as owner       │
      │  (Failover on master failure)                │
      └───────────┴───────────┴───────────┴───────────┘
```

**HA Benefits:**
- ✅ External LB distributes load across all 3 masters
- ✅ If one master fails, external LB routes to healthy masters
- ✅ MetalLB can failover IP ownership between masters
- ✅ No single point of failure

**Performance:**
- External LB adds: ~1-5ms (still negligible vs inference)
- Same internal routing as single-master setup

---

## External Access Configuration

To reach an Intel® AI for Enterprise Solutions cluster from outside the private network, configure DNS and firewall rules as follows.

### DNS Setup

**Option 1: Wildcard DNS (recommended for production)**
```
*.inference-example.com.  IN  A  203.0.113.50
```

**Option 2: Individual records**
```
llama-3-2-1b.inference-example.com.  IN  A  203.0.113.50
keycloak.inference-example.com.      IN  A  203.0.113.50
```

**Option 3: /etc/hosts (testing)**
```bash
# /etc/hosts on client machine
203.0.113.50  llama-3-2-1b.inference-example.com
203.0.113.50  keycloak.inference-example.com
```

### Firewall Configuration

**iptables (if master is the firewall):**
```bash
# Enable IP forwarding
sysctl -w net.ipv4.ip_forward=1

# Port forwarding
iptables -t nat -A PREROUTING -d 203.0.113.50 -p tcp --dport 443 \
  -j DNAT --to-destination 10.1.1.1:443

iptables -t nat -A PREROUTING -d 203.0.113.50 -p tcp --dport 80 \
  -j DNAT --to-destination 10.1.1.1:80

# Allow forwarding
iptables -A FORWARD -d 10.1.1.1 -j ACCEPT
iptables -A FORWARD -s 10.1.1.1 -j ACCEPT

# Masquerade
iptables -t nat -A POSTROUTING -s 10.1.1.0/24 -j MASQUERADE

# Restrict NFS (port 2049) and rpcbind (port 111) to cluster nodes only.
# Replace 10.1.1.2 and 10.1.1.3 with your actual node IPs.
iptables -A INPUT -p tcp --dport 2049 -s 10.1.1.2 -j ACCEPT
iptables -A INPUT -p tcp --dport 2049 -s 10.1.1.3 -j ACCEPT
iptables -A INPUT -p tcp --dport 2049 -j DROP
iptables -A INPUT -p tcp --dport 111 -s 10.1.1.2 -j ACCEPT
iptables -A INPUT -p tcp --dport 111 -s 10.1.1.3 -j ACCEPT
iptables -A INPUT -p tcp --dport 111 -j DROP

# Persist
iptables-save > /etc/iptables/rules.v4
```

> **Security note:** When using `storage_backend: nfs`, restrict NFS port 2049
> and rpcbind port 111 on the storage node to cluster nodes only. The installer
> does this automatically when `nfs_firewall_restrict: true` (the default).
> Never expose port 2049 to untrusted networks.

**External HAProxy (multi-master HA):**
```
# /etc/haproxy/haproxy.cfg
frontend k8s_https
    bind 203.0.113.50:443
    mode tcp
    default_backend k8s_masters

backend k8s_masters
    mode tcp
    balance roundrobin
    option tcp-check
    server master1 10.1.1.1:443 check
    server master2 10.1.1.2:443 check
    server master3 10.1.1.3:443 check
```

---

## Performance Optimization

Intel® AI for Enterprise Solutions' networking defaults are already tuned for near-zero overhead; the patterns below explain why and how to go further.

### Zero-Overhead Pattern

✅ **Use real node IPs in MetalLB pool**
```yaml
metallb_ip_range: "10.1.1.1/32"  # Not .240-.250
```

✅ **Hardware NAT at network edge** (dedicated firewall/router)
- Overhead: ~0.01ms (wire speed)

✅ **Direct node-to-node routing** (Calico without VXLAN if same subnet)
- Configure Calico in direct routing mode for <0.1ms pod-to-pod

✅ **IPVS mode for kube-proxy** (instead of iptables)
```yaml
# Already default in Kubespray 2.30.0
ipvs:
  strictARP: true
```

### BGP Mode (Advanced)

For **true multi-node load balancing** without leader election:

```yaml
# MetalLB BGP configuration
apiVersion: metallb.io/v1beta2
kind: BGPPeer
metadata:
  name: router
  namespace: metallb-system
spec:
  myASN: 64500
  peerASN: 64501
  peerAddress: 10.1.1.254  # Your BGP router

---
apiVersion: metallb.io/v1beta1
kind: BGPAdvertisement
metadata:
  name: bgp-advert
  namespace: metallb-system
spec:
  ipAddressPools:
    - default-pool
```

**Benefits:**
- Multiple nodes announce same IP (ECMP)
- Router distributes traffic across all nodes
- Automatic failover (BGP convergence ~1-2 seconds)

---

## Troubleshooting

Diagnostic commands for the networking layer of Intel® AI for Enterprise Solutions, in the order a request travels.

### Check MetalLB IP allocation
```bash
kubectl get svc -n envoy-gateway-system -l gateway.envoyproxy.io/owning-gateway-name
# Should show EXTERNAL-IP from MetalLB pool
```

### Verify ARP announcement
```bash
# From external machine on same network
arping -c 3 10.1.1.1
# Should see replies from master node's MAC
```

### Test connectivity
```bash
# From external client
curl -k https://10.1.1.1
# Should reach Envoy Gateway (may return 404 if no route matches)
```

### Check HTTPRoute
```bash
kubectl get httproute -n llm-inference
kubectl describe httproute llama-3-2-1b -n llm-inference
```

### Trace request path
```bash
# Enable Envoy access logs
kubectl logs -n envoy-gateway-system -l gateway.envoyproxy.io/owning-gateway-name -f

# Make test request
curl -k -H "Authorization: Bearer $TOKEN" \
  https://llama-3-2-1b.inference-example.com/v1/completions
```

---

## Summary

Intel® AI for Enterprise Solutions networking follows a small set of principles that keep on-premises deployments fast and simple to operate.

**Key Principles:**

1. **Use real node IPs** — Not synthetic `.240-.250` ranges
2. **Handle external routing at edge** — NAT/LB at firewall, not inside cluster
3. **Trust Kubernetes CNI** — Already optimized for pod-to-pod routing
4. **Network overhead is negligible** — <1% of inference latency

**Result:**
- Zero additional hops inside cluster
- Works across all deployment modes (single/multi-node/HA)
- Production-grade performance
- No compromise on inference speed

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See how this networking layer fits with platform, inference, and application layers | [Architecture & Design Document](../reference/architecture.md) |
| Set up the multi-node or HA cluster this topology assumes | [Multi-Node & BYO Cluster](topologies.md) |
| Change `gateway_request_timeout`, TLS mode, or other gateway settings | [Configuration Reference](../customize/configuration.md) |
| Route additional tenants, versions, or canaries through the gateway | [Integration Guide — Routing Patterns](../customize/integration.md#routing-patterns-host--path) |
