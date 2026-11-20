# Node Topology & Workload Placement

[← Docs Index](../README.md)

On a cluster with several machines, Toolkit Manager labels each node by its job and asks the scheduler to keep the platform services together, away from the machines that serve models.

Why you would care: model servers want whole machines. If Keycloak, PostgreSQL, and the observability stack land on the same node as an LLM, they compete for the cores that inference throughput depends on. This feature states a *preference* to the Kubernetes scheduler (the part of Kubernetes that picks a node for each pod). Because it is a preference and not a rule, pods are still placed when the preferred nodes are full or unavailable.

> [!NOTE]
> This applies to multi-node clusters only. On a single node there is nowhere else to place anything, and the labels are not applied.

## Overview

When `node_topology_enabled: true` (the default) on a multi-node cluster:

- **Every node gets a label.** Control-plane nodes get `workload-class=platform`; worker nodes get `workload-class=inference`.
- **Platform components prefer `workload-class=platform` nodes** (Keycloak, PostgreSQL, cert-manager, MetalLB, Istio, KEDA, metrics-server, Headlamp, the observability stack, the Envoy Gateway controller, and from the inference layer the KServe controller, LiteLLM, Langfuse, the Envoy AI Gateway and agentgateway controllers).
- **The preference is soft.** If no preferred node can take the pod, the scheduler places it elsewhere. Nothing fails because of it.
- **No taints are added** by this feature. A *taint* is a hard "keep out" mark on a node; see [why soft affinity](#why-does-toolkit-manager-use-soft-affinity-instead-of-taints).

What decides where **models** run is different, see [Where models run](#where-models-run).

## Configuration

### Enable/Disable

```yaml
# env/<name>/global_config.yaml
node_topology_enabled: true  # default: true
```

Set to `false` to turn off the platform preference (all pods can land anywhere). See [Turn it off](#turn-it-off) for what to run afterwards.

### Behavior by Deployment Mode

| Mode | Behavior |
|------|----------|
| **Single node** (`localhost` in `nodes.yaml`, the default) | Labels are not applied; the preference has nothing to choose between |
| **Multi-node** (`nodes.yaml` lists remote hosts) | Labels applied at the end of `install kubernetes`, and to new nodes by `add-node`; the platform preference is active |
| **Existing cluster (BYO)** | Labels are not applied: label the nodes yourself, see [Existing cluster](#existing-cluster-byo-mode) |

## How It Works

### 1. Node labels

At the end of `install kubernetes` on a multi-node cluster, the installer labels every node. A node with the `node-role.kubernetes.io/control-plane` label gets `workload-class=platform`; every other node gets `workload-class=inference`. It is the same as running:

```bash
# Control-plane nodes
kubectl label node master1 workload-class=platform

# Worker nodes
kubectl label node worker1 workload-class=inference
kubectl label node worker2 workload-class=inference
```

A control-plane node that is also listed under `nodes_workers` still counts as control-plane, so it gets `workload-class=platform`.

**File**: [`roles/kubernetes/tasks/apply_node_topology.yaml`](../../roles/kubernetes/tasks/apply_node_topology.yaml)

### 2. Platform affinity

The rule is defined **once**, in [`playbooks/includes/preflight.yaml`](../../playbooks/includes/preflight.yaml), as the shared fact `_platform_affinity`, and every platform chart or manifest uses it. Changing the rule there changes it for every component.

When `node_topology_enabled: false`, the fact is `{}` and the charts get no affinity.

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

**Components that use it**, in this repository: cert-manager, Envoy Gateway (controller), Headlamp, Istio, KEDA, Keycloak, MetalLB, metrics-server, the observability stack, PostgreSQL. In the inference repository: agentgateway, Envoy AI Gateway, KServe, Langfuse, LiteLLM.

> [!IMPORTANT]
> On a multi-node cluster built with workers, Kubernetes puts a `NoSchedule` taint on the control-plane node, and these components do not tolerate it. They then run on the workers, and the preference only matters on clusters whose control plane accepts pods (one listed under `nodes_workers` too, or with the taint removed).

### 3. The Envoy data plane

The Envoy proxy pods that receive outside HTTPS traffic are placed by a hard rule, not by the preference above. On Kubernetes, Envoy Gateway's `EnvoyProxy` resource `eg-proxy-config` (referenced from the `GatewayClass`) runs them as a DaemonSet on the control-plane nodes only: a `nodeSelector` on `node-role.kubernetes.io/control-plane` with a toleration for its taint. On OpenShift they are a plain Deployment that any node can run.

### Where models run

`model-manager` does not add a `workload-class` preference to model pods. What decides their node:

1. **The control-plane taint.** On a cluster built with workers, models cannot land on the tainted control plane, so they run on the workers.
2. **`--node <name>`.** `model-manager deploy <model> --node <name>` is optional, but when you give it, it is a *hard* rule: the pod runs on that node or stays `Pending`.
3. **AMX.** A catalog that requires Intel AMX (`require_amx`) only places a CPU model on nodes labelled `feature.node.kubernetes.io/cpu-cpuid.AMXBF16=true`.
4. **Resources.** Otherwise the scheduler picks any node with enough free CPU, memory or GPUs.

The `workload-class=inference` label is applied to workers, but no component currently selects on it.

## Benefits

- **Resource isolation**: platform services and models do not compete for the same cores where the control plane can take the platform pods.
- **Control plane stability**: the control plane is not loaded with models.
- **Flexible scheduling**: soft preferences never block a pod; draining or cordoning a node works as usual.

## Verification

### Check Node Labels

```bash
kubectl get nodes -L workload-class
```

Expected output (multi-node):
```
NAME      STATUS   ROLES           ...   WORKLOAD-CLASS
master1   Ready    control-plane   ...   platform
worker1   Ready    <none>          ...   inference
worker2   Ready    <none>          ...   inference
```

### Check Pod Placement

The `NODE` column shows where each pod runs:

```bash
# Platform components
kubectl get pods -n keycloak -o wide
kubectl get pods -n postgresql -o wide
kubectl get pods -n envoy-gateway-system -o wide

# Models
kubectl get pods -n llm-inference -o wide
```

### Verify the Affinity Rule

```bash
kubectl get pods -n keycloak -o jsonpath='{range .items[*]}{.metadata.name}{": "}{.spec.affinity}{"\n"}{end}'
```

Each platform pod shows a `preferredDuringSchedulingIgnoredDuringExecution` entry for `workload-class In [platform]`. Empty output means `node_topology_enabled` was `false` when the component was installed.

## Troubleshooting

### Pods Not Landing on Preferred Nodes

**Cause**: the affinity is a **preference**, not a requirement. The scheduler places the pod elsewhere when:
- the preferred node has a taint the pod does not tolerate (the usual control-plane case, see the note above)
- the preferred nodes lack CPU or memory
- the node is cordoned or drained
- another rule wins (a volume bound to one node, a topology spread constraint)

**Resolution**: check the node's taints, capacity and the pod's requests:
```bash
kubectl describe node <node-name>   # Taints, Allocatable, Allocated resources
kubectl top nodes
```

### Existing Cluster (BYO Mode)

On an existing cluster the installer does not label nodes. The platform charts still carry the preference, so labelling is all it takes:

1. Label the nodes:

   ```bash
   kubectl label nodes master1 master2 master3 workload-class=platform
   kubectl label nodes worker1 worker2 worker3 workload-class=inference
   ```

2. Pods already running stay where they are; the preference is used only when a pod is scheduled. To move one now, restart its workload, for example:

   ```bash
   kubectl rollout restart deployment -n metallb-system
   ```

### Turn it off

1. Set `node_topology_enabled: false` in `env/<name>/global_config.yaml`.
2. Re-run the install of each installed layer, so the charts are applied again without the affinity:

   ```bash
   ./toolkit_manager.sh install platform --env <name>
   ./toolkit_manager.sh install inference --env <name>
   ```

3. Optional: the labels are not removed by the steps above. Remove them by hand:

   ```bash
   kubectl label nodes --all workload-class-
   ```

## Architecture Notes

### Why Does Toolkit Manager Use Soft Affinity Instead of Taints?

**Taints** enforce hard placement rules (`NoSchedule`, `NoExecute`). Rejected because:
- Hard failures if resources are constrained
- Breaks existing workloads without tolerations
- Complicates DaemonSet deployments (CNI, monitoring)
- Inflexible for mixed workloads

**Soft affinity** balances separation with flexibility — the scheduler respects the preference but can override it when needed.

### Weight

The platform rule has weight `100`, the highest a preference can have. The scheduler adds up the weights of the preferences each node satisfies and favours the node with the highest score.

### Label Stability

Labels are applied **idempotently**: re-running the installer neither duplicates nor conflicts with them. `add-node` labels the nodes again when `node_topology_enabled` is true, the new node included.

## Related Docs

| If you want to… | Go to |
|---|---|
| Enable/disable node topology and set related flags in `global_config.yaml` | [Configuration Reference](configuration.md) |
| Pin model pods to specific CPUs/NUMA domains once they land on the right node | [NRI CPU Balloons](nri_cpu_balloons.md) |
| Deploy across multiple nodes in the first place | [Multi-Node & BYO Cluster](../deploy/topologies.md) |
| Add or remove nodes from an already-provisioned cluster | [Node Scaling](node_scaling.md) |
| See where node-level placement fits in the overall design | [Architecture & Design Document](../reference/architecture.md) |

## External References

- [Kubernetes Node Affinity](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#node-affinity) — official docs on the `preferredDuringSchedulingIgnoredDuringExecution` rules this feature uses
- [Taints and Tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/) — the hard-enforcement alternative this design deliberately avoids (see [Why Does Toolkit Manager Use Soft Affinity Instead of Taints?](#why-does-toolkit-manager-use-soft-affinity-instead-of-taints) above)
