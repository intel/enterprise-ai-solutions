# Node Scaling — add-node / remove-node

[← Docs Index](../README.md)

Grow or shrink an already-provisioned cluster without a full teardown/reinstall, using Kubespray's own `scale.yml` and `remove-node.yml` playbooks.

## Overview

Two CLI actions, both driven entirely from `env/<name>/nodes.yaml` — neither takes a node name on the command line:

| Action | Kubespray playbook | Scope |
|---|---|---|
| `add-node` | `scale.yml` | **Worker nodes only** |
| `remove-node` | `remove-node.yml` | Any node except the first control-plane/etcd host |

The workflow is always: **edit `nodes.yaml`, then run the action**. The installer regenerates the inventory from the edited file and diffs it against the live cluster (`kubectl get nodes`) to figure out which host(s) changed — there is nothing else to specify.

```bash
./es_auto_installer.sh add-node --dry-run     # preview after editing nodes.yaml
./es_auto_installer.sh add-node                # scale in the new worker(s)
./es_auto_installer.sh remove-node --dry-run   # preview after deleting entries
./es_auto_installer.sh remove-node             # remove the dropped node(s)
```

## What's supported

| Scenario | Supported? |
|---|---|
| Installer-managed cluster, no ERAG deployed | Yes |
| Adding worker node(s) via `add-node` | Yes |
| Removing node(s) via `remove-node` (except the first control-plane/etcd host) | Yes |

## What's not supported

| Scenario | Why |
|---|---|
| BYO cluster (`existing_kubernetes` set in `global_config.yaml`) | A bring-your-own cluster is not managed by Kubespray at all |
| Adding control-plane/etcd node(s) via `add-node` | Kubespray's `scale.yml` cannot add control-plane/etcd members — use `install kubernetes` instead (re-runs `cluster.yml`) |
| Removing the first control-plane/etcd node via `remove-node` | Kubespray refuses to remove it directly — reorder `nodes_control_plane` and re-run `install kubernetes` first |
| Cluster with ERAG deployed | Both actions only ever touch the `kubernetes` component — ERAG's own Helm/pipeline config is never re-applied, so any node-specific placement it relies on isn't accounted for |


## Adding worker nodes

1. Append entries to `nodes_workers` in `env/<name>/nodes.yaml`:

   ```yaml
   nodes_workers:
     - ip: 10.0.0.22
       hostname: worker3
   ```

2. Preview, then apply:

   ```bash
   ./es_auto_installer.sh add-node --dry-run --env <name>
   ./es_auto_installer.sh add-node --env <name>
   ```

3. Behind the scenes: the inventory is regenerated from `nodes.yaml`, the new hostname(s) are diffed against the live cluster, the new node(s) go through a pre-flight (SSH reachability, not-already-joined, control-plane API server reachability, internet/proxy reachability — see [Safety](#safety) below; fails fast here rather than deep inside a 10+ minute Kubespray run), Kubespray's `scale.yml` is run limited to the new node(s) + control-plane (its cert-refresh play runs there), then the kubelet CPU reservation and `workload-class` topology label are applied to just the new node(s), and the installer waits for them to go `Ready`.

### Control-plane nodes are not supported by `add-node`

Kubespray's `scale.yml` cannot add control-plane or etcd members — that requires a full `cluster.yml` re-run with the new host appended to the **end** of `nodes_control_plane`. If `add-node` detects a new entry under `nodes_control_plane`, it refuses and points you at:

```bash
./es_auto_installer.sh install --env <name> kubernetes
```

## Removing nodes

1. Delete the entry from `nodes_control_plane` or `nodes_workers` in `env/<name>/nodes.yaml`.
2. Preview, then apply:

   ```bash
   ./es_auto_installer.sh remove-node --dry-run --env <name>
   ./es_auto_installer.sh remove-node --env <name>
   ```
3. Before anything runs, the installer lists any real workload pods (excluding DaemonSets and static/mirror pods) running on the node(s) about to be removed — so you know what the drain is about to evict before you confirm. Then Kubespray's `remove-node.yml` drains, resets, and removes the node from the cluster (and, for a control-plane/etcd node, from etcd membership) before the installer deletes its stale per-node `host_vars` file.

   ```
   remove-node: node(s) to remove: worker1
   WARN:   worker1 is running workload(s) that will be evicted:
       - default/my-model
   About to run Kubespray remove-node.yml for: worker1 — this drains, resets, and removes those node(s) from the cluster. Proceed? [y/N]
   ```

   If the workload has no `PodDisruptionBudget`, the drain evicts it immediately — there is no built-in protection against removing a node that's the only place a model happens to be running. Check what's listed before confirming.

   If the node being removed is the **last worker**, a second warning fires — it checks whether the control-plane node currently carries the standard `NoSchedule` taint (it does if the cluster was ever provisioned multi-node from the start; it doesn't if it started single-node and workers were added later via `add-node`, since kubeadm only sets that taint at cluster bring-up, not on a later `add-node`/`scale.yml` run). Tainted means nothing will be schedulable anywhere until a worker comes back; untainted means the cluster just drops to single-node capacity, which is a supported topology on its own.

### The first control-plane/etcd node cannot be removed this way

Kubespray uses the first host in `kube_control_plane` (which this installer's generated inventory always mirrors into `etcd` too) as a reference node for cert generation and etcd bootstrap, and refuses to remove it directly. If the node you deleted from `nodes.yaml` is currently first, `remove-node` refuses with a message telling you to:

1. Reorder `nodes_control_plane` in `nodes.yaml` so a different host is first.
2. Run `./es_auto_installer.sh install --env <name> kubernetes` to apply the reorder via `cluster.yml`.
3. Retry `remove-node`.

## Safety

### Built into Kubespray's `scale.yml`

1. **Standard preinstall checks** run against the new node(s) the same as any Kubespray play — OS/fact gathering, `kube_service_addresses`/`kube_pods_subnet` CIDR validation, proxy config sanity checks in `/etc/apt`, etc.
2. **No support for adding control-plane/etcd members** — `scale.yml` only onboards workers; see [Control-plane nodes are not supported by `add-node`](#control-plane-nodes-are-not-supported-by-add-node) above.

### Built into `add-node`'s pre-flight

Before `scale.yml` ever runs, against just the new node(s):

1. **SSH reachability** (`ping`) — hard fail if unreachable, before anything is provisioned.
2. **Not already joined to a cluster** — fails if `/etc/kubernetes/kubelet.conf` already exists on the node, rather than letting a stale or foreign join corrupt either cluster.
3. **Duplicate hostname/IP within `nodes.yaml`** — checked even earlier, as part of inventory generation (applies to `install`/`remove-node` too, not just `add-node`): fails if any two entries across `nodes_control_plane`/`nodes_workers` share an `ip` or `hostname`.
4. **Control-plane API server (port 6443) reachability** — `scale.yml`'s `kubeadm join` needs this; failing here is faster and clearer than a join timeout deep inside the Kubespray run.
5. **Internet/proxy reachability** (same check a fresh `install` runs against every node) — a warning only, since a private registry mirror can make it a non-issue.

### Known gaps in `add-node`'s pre-checks

Not checked, and each would currently only surface as a Kubespray failure partway through `scale.yml` rather than a fast, clear pre-check:

- **Calico's data path (BGP/VXLAN) between the new node and the rest of the cluster** — there's nothing to check before the node joins; `calico-node` isn't running there yet, so this is inherently a post-join concern, not a pre-flight one.
- **Whether the node has adequate resources** (CPU/memory/disk) for what's expected to run there — the mechanics are easy (Ansible already gathers these facts), but there's no minimum threshold defined anywhere in this codebase to check against yet.

### Built into Kubespray's `remove-node.yml`

1. **Explicit target required** — `-e node=<name>` must be set and non-empty; the play asserts this and fails with "No nodes specified for removal" otherwise.
2. **Interactive confirmation by default** — prompts "Type 'yes' to delete nodes" unless `-e skip_confirmation=true` is passed.
3. **Graceful drain before removal** — cordons and evicts workloads from the node before touching it (seen in our test log: `TASK [remove_node/pre_remove : Remove-node | Drain node except daemonsets resource]`).
4. **Full OS reset by default** (`reset_nodes=true`) — stops kubelet/containerd, removes `/etc/kubernetes`, CNI state, iptables rules, etc., leaving the node clean. Can be skipped (`reset_nodes=false`) for an unreachable node, but only paired with an explicit `allow_ungraceful_removal=true` — a second, deliberate opt-out.
5. **Proper etcd membership removal** for control-plane/etcd nodes — avoids leaving a stale/unhealthy etcd member that could threaten quorum.
6. **Refuses to remove the first `kube_control_plane`/etcd host directly** — that host is the reference used for cert generation and etcd bootstrap elsewhere in Kubespray; removing it needs a manual inventory reorder + `cluster.yml` re-run first.
7. **Inventory is untouched by the play itself** — the node stays listed during/after the run; removing the entry is left to the caller, as a deliberate checkpoint.

### Built into `es_auto_installer.sh`

- **`--dry-run`** prints the computed node diff and the exact Kubespray invocation, without running anything.
- A `confirm()` gate (y/N or `--force`) — separate from Kubespray's own `skip_confirmation`, which we pass through specifically so Kubespray's prompt doesn't block a non-interactive run; ours is the one that actually gates the destructive action.
- **No diff, no action** — if `nodes.yaml` already matches the live cluster, both actions report "nothing to do" and exit, never invoking Kubespray.
- Our own first-control-plane/etcd guard, checked from the current inventory *before* anything runs (mirrors #6 above, at the wrapper level, so the error message can point back at `nodes.yaml`).
- Inventory regeneration is deliberately deferred until *after* `remove-node.yml` succeeds, so Kubespray always gets a node it can still reach.
- **Workload visibility before you confirm** — lists real (non-DaemonSet, non-static-pod) workloads running on the node(s) about to be removed, so a model or service that's only running there doesn't get evicted as a surprise.
- **Last-worker warning** — flags when the removal would leave zero worker nodes, and separately reports whether the control-plane node currently has the `NoSchedule` taint (so you know whether that actually means "nothing schedulable anywhere" or just "back to single-node"). A warning, not a block — dropping to zero workers is a valid, already-supported topology when it's intentional.
- **`nri_cpu_balloons` reminder** — if CPU pinning (`kubernetes_cpu_policy: nri-balloons`) is enabled, both actions print a reminder to re-run `install nri_cpu_balloons` afterward: it's a separate component, not part of `kubernetes`, so it isn't re-applied automatically.

## Related Docs

| If you want to… | Go to |
|---|---|
| Deploy across multiple nodes in the first place | [Multi-Node & BYO Cluster](../deploy/topologies.md) |
| Understand the `workload-class` labels re-applied to new nodes | [Node Topology & Workload Placement](node_topology.md) |
| Look up every `global_config.yaml` / `nodes.yaml` field | [Configuration Reference](configuration.md) |

## External References

- [Kubespray — Adding/replacing a node](https://github.com/kubernetes-sigs/kubespray/blob/v2.30.0/docs/operations/nodes.md) — upstream docs for `scale.yml` and `remove-node.yml`, including the first-control-plane-node limitation this doc summarizes
