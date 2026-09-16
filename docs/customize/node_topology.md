# Node Topology & Workload Placement

[← Docs index](../README.md)

On a multi-node cluster, platform pods prefer control-plane nodes and inference
pods prefer workers. Those preferences are soft: the scheduler can place a pod
on the other class of node if resources are tight. The installer does not use
taints, so a shortage of workers will not leave inference pods unschedulable.

## When it is on

`node_topology_enabled: true` is the default on multi-node installs.

- **Platform components** (Keycloak, PostgreSQL, Envoy Gateway, cert-manager, MetalLB, KServe controller, AI Gateway controller) prefer control-plane nodes
- **Inference workloads** (vLLM pods, model serving) prefer worker nodes

## Configuration

### Enable/Disable

```yaml
# env/<name>/global_config.yaml
node_topology_enabled: true  # default: true
```

Set to `false` to disable workload separation (all pods can land anywhere).

### Behavior by Deployment Mode

| Mode | Behavior |
|------|----------|
| **Localhost** (default) | No-op (all pods co-located) |
| **Remote/Multi-node** (inventory has remote hosts) | Labels applied, soft affinity active |
| **Existing cluster (BYO)** | User must label nodes manually if desired |

## How It Works

### 1. Node Labeling (Kubernetes Role)

Multi-node clusters get labeled during Kubernetes provisioning:

```bash
# Control-plane nodes
kubectl label node master1 workload-class=platform

# Worker nodes  
kubectl label node worker1 workload-class=inference
kubectl label node worker2 workload-class=inference
```

**File**: [`roles/kubernetes/tasks/apply_node_topology.yaml`](../../roles/kubernetes/tasks/apply_node_topology.yaml)

### 2. Pod Affinity (Component Roles)

Affinity rules are defined **once** in [`playbooks/includes/preflight.yaml`](../../playbooks/includes/preflight.yaml) as shared facts (`_platform_affinity`, `_inference_affinity`) and referenced by every component. This keeps the placement policy in a single source of truth, changing the rule updates every component.

When `node_topology_enabled: false`, the facts resolve to `{}` and Helm charts receive empty affinity (no effect).

#### Platform Components (prefer control-plane)

All platform Helm charts reference `_platform_affinity`:

```yaml
# Example: roles/metallb/tasks/install.yaml
values:
  controller:
    affinity: "{{ _platform_affinity | default({}) }}"
```

The fact expands to:

```yaml
nodeAffinity:
  preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      preference:
        matchExpressions:
          - key: workload-class
            operator: In
            values: [platform]
```

**Affected components**:
- Keycloak (StatefulSet) ([`roles/keycloak/templates/keycloak.yaml.j2`](../../roles/keycloak/templates/keycloak.yaml.j2))
- PostgreSQL (CNPG Cluster) ([`roles/postgresql/templates/cnpg-cluster.yaml.j2`](../../roles/postgresql/templates/cnpg-cluster.yaml.j2))
- cert-manager (controller + webhook + cainjector) ([`roles/cert_manager/tasks/install.yaml`](../../roles/cert_manager/tasks/install.yaml))
- MetalLB controller ([`roles/metallb/tasks/install.yaml`](../../roles/metallb/tasks/install.yaml))
- Envoy Gateway **controller + data plane** ([`roles/envoy_gateway/tasks/install.yaml`](../../roles/envoy_gateway/tasks/install.yaml))
- Envoy AI Gateway **controller + data plane** ([`ext/enterprise.ai-inference/roles/envoy_ai_gateway/tasks/install.yaml`](https://github.com/intel/enterprise-inference/blob/main/roles/envoy_ai_gateway/tasks/install.yaml))
- KServe controller ([`ext/enterprise.ai-inference/roles/kserve/tasks/install.yaml`](https://github.com/intel/enterprise-inference/blob/main/roles/kserve/tasks/install.yaml))

> **Envoy data-plane pinning**: The actual Envoy proxy pods (which handle external
> HTTPS traffic, not just the controller) are pinned via an `EnvoyProxy` CRD
> referenced from the `GatewayClass`, see `eg-proxy-config` / `ai-gateway-proxy-config`.

#### Inference Workloads (prefer workers)

Inference pods include **dual preference** (workload-class + fallback):

```yaml
affinity:
  nodeAffinity:
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        preference:
          matchExpressions:
            - key: workload-class
              operator: In
              values: [inference]
      - weight: 90
        preference:
          matchExpressions:
            - key: node-role.kubernetes.io/control-plane
              operator: DoesNotExist
```

**Affected templates** (static YAML, processed by model_manager at runtime, not Ansible):
- LLMInferenceService ([`ext/enterprise.ai-inference/model_manager/templates/llm-inference-service.yaml`](https://github.com/intel/enterprise-inference/blob/main/model_manager/templates/llm-inference-service.yaml))
- InferenceService ([`ext/enterprise.ai-inference/model_manager/templates/inference-service.yaml`](https://github.com/intel/enterprise-inference/blob/main/model_manager/templates/inference-service.yaml))

## Benefits

### Resource Isolation
- Platform components don't compete with inference for CPU/memory
- Inference workloads get dedicated worker resources

### NUMA Optimization
- Workers can be dedicated to model serving (NUMA affinity, huge pages)
- Control-plane nodes stable for cluster management

### Control Plane Stability
- Masters not overloaded by inference pods
- Cluster operations remain responsive

### Flexible Scheduling
- Soft preferences allow scheduler overrides if needed
- No hard failures from resource constraints
- Works with node maintenance (drain/cordon)

## Verification

### Check Node Labels

```bash
kubectl get nodes --show-labels | grep workload-class
```

Expected output (multi-node):
```
master1   Ready   control-plane   ...   workload-class=platform
worker1   Ready   <none>          ...   workload-class=inference
worker2   Ready   <none>          ...   workload-class=inference
```

### Check Pod Placement

```bash
# Platform components (should be on control-plane)
kubectl get pods -n keycloak -o wide
kubectl get pods -n postgresql -o wide
kubectl get pods -n envoy-gateway-system -o wide

# Inference workloads (should be on workers)
kubectl get pods -n llm-services -o wide
kubectl get pods -n inference-services -o wide
```

### Verify Affinity Rules

```bash
# Check platform component affinity
kubectl get deployment keycloak-operator -n keycloak -o yaml | grep -A 10 affinity

# Check inference pod affinity
kubectl get pod <inference-pod-name> -n llm-services -o yaml | grep -A 15 affinity
```

## Troubleshooting

### Pods Not Landing on Preferred Nodes

**Cause**: Soft affinity is a **preference**, not a requirement. Scheduler may override if:
- Preferred nodes lack resources (CPU/memory)
- Node is cordoned/drained
- Other constraints (PVC affinity, topology spread)

**Resolution**: Check node capacity and pod resource requests:
```bash
kubectl top nodes
kubectl describe node <node-name>
```

### Existing Cluster (BYO Mode)

If using an existing cluster, labels are **not applied automatically**. Label manually:

```bash
# Label control-plane nodes
kubectl label nodes master1 master2 master3 workload-class=platform

# Label worker nodes
kubectl label nodes worker1 worker2 worker3 workload-class=inference
```

Then redeploy components for affinity to take effect:
```bash
./es_auto_installer.sh teardown platform --env <name>
./es_auto_installer.sh install platform --env <name>
```

### Disable Topology (Emergency Override)

If soft affinity causes issues:

```bash
# Disable in config
vim env/<name>/global_config.yaml  # set node_topology_enabled: false

# Redeploy affected layers
./es_auto_installer.sh teardown --all --env <name>
./es_auto_installer.sh install --all --env <name>
```

Or remove labels manually:
```bash
kubectl label nodes --all workload-class-
```

## Architecture Notes

### Why Does Intel® AI for Enterprise Solutions Use Soft Affinity Instead of Taints?

**Taints** enforce hard placement rules (`NoSchedule`, `NoExecute`). Rejected because:
- Hard failures if resources constrained
- Breaks existing workloads without tolerations
- Complicates DaemonSet deployments (CNI, monitoring)
- Inflexible for mixed workloads

**Soft affinity** balances separation with flexibility, scheduler respects preferences but can override when needed.

### Weight Values

- **100**: Primary preference (workload-class match)
- **90**: Fallback preference (avoid control-plane for inference)

Higher weight = stronger preference. Scheduler sums weights across all rules.

### Label Stability

Labels are applied **idempotently**, re-running installer won't duplicate or conflict. Safe to re-apply after node addition/replacement.

## Related Docs

| If you want to… | Go to |
|---|---|
| Enable/disable node topology and set related flags in `global_config.yaml` | [Configuration Reference](configuration.md) |
| Pin model pods to specific CPUs/NUMA domains once they land on the right node | [NRI CPU Balloons](nri_cpu_balloons.md) |
| Deploy across multiple nodes in the first place | [Multi-Node & BYO Cluster](../deploy/topologies.md) |
| See where node-level placement fits in the overall design | [Architecture & Design](../meet/architecture.md) |

## External References

- [Kubernetes Node Affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#node-affinity), official docs on the `preferredDuringSchedulingIgnoredDuringExecution` rules this feature uses
- [Taints and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/), the hard-enforcement alternative this design deliberately avoids (see [Why Does Intel® AI for Enterprise Solutions Use Soft Affinity Instead of Taints?](#why-does-intel-ai-for-enterprise-solutions-use-soft-affinity-instead-of-taints) above)
