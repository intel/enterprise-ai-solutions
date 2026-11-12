# Node Scaling — add-node / remove-node

[← Docs Index](../README.md)

Grow or shrink an already-provisioned cluster without a full teardown/reinstall, using Kubespray's own `scale.yml` and `remove-node.yml` playbooks.

> **Note:** ADVANCED OPERATION: PROCEED ONLY IF YOU KNOW WHAT YOU'RE DOING — always run with `--dry-run` first.

## Overview

Two CLI actions, both driven entirely from `env/<name>/nodes.yaml` — neither takes a node name on the command line:

| Action | Kubespray playbook | Scope |
|---|---|---|
| `add-node` | `scale.yml` | **Worker nodes only** |
| `remove-node` | `remove-node.yml` | **Worker nodes only** |

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
| A control-plane node that is also a worker (an identical entry under both `nodes_control_plane` and `nodes_workers`) | Yes, as long as it stays in both lists — `add-node`/`remove-node` treat it as control-plane, so changing its role (worker on/off) needs `install kubernetes`, and deleting it from both lists is a control-plane removal (refused) |
| Removing worker node(s) via `remove-node` | Yes |

## What's not supported

| Scenario | Why |
|---|---|
| BYO cluster (`existing_kubernetes` set in `global_config.yaml`) | A bring-your-own cluster is not managed by Kubespray at all |
| Adding control-plane/etcd node(s) via `add-node` | Kubespray's `scale.yml` cannot add control-plane/etcd members — use `install kubernetes` instead (re-runs `cluster.yml`) |
| Removing control-plane/etcd node(s) via `remove-node` | Refused: see [Control-plane nodes are not removed](#control-plane-nodes-are-not-removed) |
| Cluster with ERAG deployed | Both actions only ever touch the `kubernetes` component — ERAG's own Helm/pipeline config is never re-applied, so any node-specific placement it relies on isn't accounted for. Both refuse when any ERAG-only namespace (`rag-ui`, `chat-history`, `edp`, `fingerprint`, `vdb`, `erag-gateway`, `auth-apisix`) exists |
| Hand-edited inventory (`env/<name>/inventory/hosts.yaml`, no `nodes.yaml`) | Both actions find their target node(s), SSH details and control-plane membership from `nodes.yaml`; without it they stop before doing anything. Convert the inventory to `nodes.yaml` first |
| Rack-scale (`rack_scale_enabled: true`) | The inventory comes from `rack-profile.yaml`, not `nodes.yaml`, and neither action runs the fabric roles (netplan, sysctl, BGP). Both refuse a rack-scale env before doing anything |
| `add-node` with `storage_backend: local-path`, or `custom` with `storage_custom_rwx: false` | Both serve a single node: the storage check refuses them on more than one, so every later install, validate and teardown would stop. `add-node` refuses them up front; move the env to NFS or Ceph first |
| Per-node setup of other components on a new node | `add-node` runs only the kubernetes component (plus NFS exports and NRI balloons). A new node is not made a Ceph OSD node, does not get NetApp Trident's NFS client packages, and is not marked eligible for Kata. Re-run `install` for `rook_ceph_storage`, `netapp_trident_csi` or `kata_containers` if the new node needs them |


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

3. What `add-node` does:
   1. Regenerates the inventory and diffs `nodes.yaml` against the live cluster to find the new node(s).
   2. Runs a pre-flight on them: SSH, not already joined, API server and internet reachability (see [Safety](#safety)).
   3. Computes their kubelet CPU reservation, then runs Kubespray `scale.yml` for just the new node(s).
   4. Applies the `workload-class` label (and NFS export, on NFS storage), waits for `Ready` and for the node's DaemonSet pods (calico-node, kube-proxy, ...), and — if `nri_cpu_balloons` is installed — creates each node's own `node.<NAME>` BalloonsPolicy. Existing nodes and running models are not touched.

   New nodes join with an `ei.intel.com/add-node-pending:NoSchedule` taint that is removed only after the last step, so nothing is scheduled on a half-configured node. If a step fails, re-run `add-node` to resume.

### Control-plane nodes are not supported by `add-node`

Kubespray's `scale.yml` cannot add control-plane or etcd members — that requires a full `cluster.yml` re-run with the new host appended to the **end** of `nodes_control_plane`. If `add-node` detects a new entry under `nodes_control_plane`, it refuses and points you at:

```bash
./es_auto_installer.sh install --env <name> kubernetes
```

### After adding a node

Kubernetes does not move running pods onto a new node. Models already deployed stay where they are; only new or restarted pods can be scheduled on it. To use the new node:

- Deploy new models, or undeploy and redeploy an existing one (`./model-manager undeploy <model>` then `deploy <model> --wait`).
- `model-manager deploy --node <name>` is a *preference*, not a requirement — if the node doesn't qualify, the pod lands elsewhere. Check where it went with `kubectl get pods -n llm-inference -o wide`.

## Removing nodes

1. Delete the entry from `nodes_workers` in `env/<name>/nodes.yaml`.
2. Preview, then apply:

   ```bash
   ./es_auto_installer.sh remove-node --dry-run --env <name>
   ./es_auto_installer.sh remove-node --env <name>
   ```
3. Before anything runs, the installer lists any real workload pods (excluding DaemonSets and static/mirror pods) running on the node(s) about to be removed — so you know what the drain is about to evict before you confirm. Then Kubespray's `remove-node.yml` drains, resets, and removes the node from the cluster before the installer deletes its stale per-node `host_vars` file.

   ```
   remove-node: node(s) to remove: worker1
   WARN:   worker1 is running workload(s) that will be evicted:
       - default/my-model
   About to run Kubespray remove-node.yml for: worker1 — this drains, resets, and removes those node(s) from the cluster. Proceed? [y/N]
   ```

   If the workload has no `PodDisruptionBudget`, the drain evicts it immediately — check what's listed before confirming. Before that, a simulation refuses the removal outright (see [Preflight simulation](#preflight-simulation-capacity--spread--local-data--last-capable-node) below) if evicting these pods would leave a workload with nowhere to go — this is where "the only place a model happens to be running" is actually caught, not just displayed.

   If the node being removed is the **last worker**, a second warning fires — it checks whether the control-plane node currently carries the standard `NoSchedule` taint (it does if the cluster was ever provisioned multi-node from the start; it doesn't if it started single-node and workers were added later via `add-node`, since kubeadm only sets that taint at cluster bring-up, not on a later `add-node`/`scale.yml` run). Tainted means nothing will be schedulable anywhere until a worker comes back; untainted means the cluster just drops to single-node capacity, which is a supported topology on its own.

### Control-plane nodes are not removed

`remove-node` refuses a node listed under `nodes_control_plane`. Every control-plane node is also an etcd member: `remove-node.yml` takes it out of etcd, but [Kubespray's own procedure](https://github.com/kubernetes-sigs/kubespray/blob/v2.31.0/docs/operations/nodes.md#removing-an-etcd-node) also needs `cluster.yml` re-run and `--etcd-servers` checked on every remaining control-plane node, and each node's `nginx-proxy` would keep the removed API server. Kubespray also stops on an even etcd count, and cannot remove the first control-plane node at all.

### A node that is already gone (`--node-lost`)

For a node that is unreachable or `NotReady`, use:

```bash
./es_auto_installer.sh remove-node --node-lost --env <name>
```

This skips the drain and the PodDisruptionBudget check and does not reset the node's OS (`allow_ungraceful_removal=true`, `reset_nodes=false`) — only etcd membership and cluster bookkeeping are cleaned up. Every target must actually be `NotReady`, so the flag can't be used to skip the drain on a healthy node. The capacity simulation still runs.

## Safety

### Built into Kubespray's `scale.yml`

1. **Standard preinstall checks** run against the new node(s) the same as any Kubespray play — OS/fact gathering, `kube_service_addresses`/`kube_pods_subnet` CIDR validation, proxy config sanity checks in `/etc/apt`, etc.
2. **No support for adding control-plane/etcd members** — `scale.yml` only onboards workers; see [Control-plane nodes are not supported by `add-node`](#control-plane-nodes-are-not-supported-by-add-node) above.

### Built into `add-node`'s pre-flight

Before `scale.yml` ever runs, against just the new node(s):

1. **SSH reachability** — the installer connects with the credentials in `nodes.yaml` before the confirm prompt (and before `--dry-run` exits).
2. **Not already joined to a cluster** — fails if `/etc/kubernetes/kubelet.conf` already exists on the node, rather than letting a stale or foreign join corrupt either cluster.
3. **No Docker installed** — fails if the node has a `docker.service` unit, running or not. Kubespray removes any container runtime other than containerd: it kills and deletes every Docker container, uninstalls Docker and deletes `/var/lib/docker` and `/etc/docker`, so all images, volumes and container data are lost. This installer never lets that happen. Back up and uninstall Docker yourself, or use a different node. `install kubernetes` runs the same check against every node (and the controller itself in single-node localhost mode) when it provisions a new cluster; on a running cluster Kubespray does not run, so it skips the check.
4. **Duplicate hostname/IP within `nodes.yaml`** — checked even earlier, as part of inventory generation (applies to `install`/`remove-node` too, not just `add-node`): fails if any two entries across `nodes_control_plane`/`nodes_workers` share an `ip` or `hostname`.
5. **Control-plane API server (port 6443) reachability** — `scale.yml`'s `kubeadm join` needs this; failing here is faster and clearer than a join timeout deep inside the Kubespray run.
6. **Internet/proxy reachability** (same check a fresh `install` runs against every node) — a warning only, since a private registry mirror can make it a non-issue.

### Known gaps in `add-node`'s pre-checks

Not checked, and each would currently only surface as a Kubespray failure partway through `scale.yml` rather than a fast, clear pre-check:

- **Calico's data path (BGP/VXLAN) between the new node and the rest of the cluster.** Before the node joins, `calico-node` isn't running there. After the join, `add-node` waits for the node's DaemonSet pods to be Ready and keeps the pending taint if they are not, but it does not test traffic between pods on different nodes. A large node can need more than Kubespray's default `calico_node_memory_limit` (500M): calico-node is then OOM-killed and the node stays tainted. Set a higher limit in `env/<name>/kubespray_extra.yml`.
- **The checks on the new node itself under `--dry-run`.** The dry run only checks SSH: "already joined to a cluster", Docker, API server and internet reachability are checked by the real run, before `scale.yml`.
- **Whether the node has adequate resources** (CPU/memory/disk) for what's expected to run there — the mechanics are easy (Ansible already gathers these facts), but there's no minimum threshold defined anywhere in this codebase to check against yet.

### Built into Kubespray's `remove-node.yml`

1. **Explicit target required** — `-e node=<name>` must be set and non-empty; the play asserts this and fails with "No nodes specified for removal" otherwise.
2. **Interactive confirmation by default** — prompts "Type 'yes' to delete nodes" unless `-e skip_confirmation=true` is passed.
3. **Graceful drain before removal** — cordons and evicts workloads from the node before touching it (seen in our test log: `TASK [remove_node/pre_remove : Remove-node | Drain node except daemonsets resource]`).
4. **OS reset by default** (`reset_nodes=true`) — stops kubelet and containerd and removes `/etc/kubernetes` and the CNI state. It does not leave the node fully clean: `cali-*`/`KUBE-*` iptables chains and their ipsets remain, and containerd is stopped with its binary gone. Re-adding the machine as it is works; flush the chains before reusing it for anything else. The reset can be skipped (`reset_nodes=false`) for an unreachable node, but only paired with an explicit `allow_ungraceful_removal=true` — a second, deliberate opt-out.
5. **Proper etcd membership removal** for control-plane/etcd nodes — avoids leaving a stale/unhealthy etcd member that could threaten quorum.
6. **Refuses to remove the first `kube_control_plane`/etcd host directly** — that host is the reference used for cert generation and etcd bootstrap elsewhere in Kubespray.
7. **Inventory is untouched by the play itself** — the node stays listed during/after the run; removing the entry is left to the caller, as a deliberate checkpoint.

### Built into `es_auto_installer.sh`

- **`--dry-run`** prints the computed node diff and the exact Kubespray invocation, and changes nothing.
- A `confirm()` gate (y/N or `--force`) — separate from Kubespray's own `skip_confirmation`, which we pass through specifically so Kubespray's prompt doesn't block a non-interactive run; ours is the one that actually gates the destructive action.
- **No diff, no action** — if `nodes.yaml` already matches the live cluster, both actions report "nothing to do" and exit, never invoking Kubespray. A `remove-node` whose post phase failed (the node is gone from the cluster and from `nodes.yaml`, but still in the inventory) runs the post phase again instead.
- **`nodes.yaml`, the inventory and the cluster must agree.** `add-node` refuses while a joined node is missing from `nodes.yaml` (its prep pass would drop that node from the inventory), and `remove-node` refuses a node that is no longer in the inventory (Kubespray could not reach it).
- **Health after `remove-node`** is judged against the findings from before the removal: only new problems fail the command, and pods the drain evicted get a minute to be scheduled.
- **Fails closed when the cluster can't be read.** If `kubectl` can't list the cluster's nodes or namespaces, both actions stop with the error rather than diffing `nodes.yaml` against an empty list. `remove-node` does the same for PodDisruptionBudgets and the preflight simulation: a check that didn't run refuses the removal, it never passes it.
- **ERAG guard** — both actions refuse when ERAG is deployed (see [What's not supported](#whats-not-supported)).
- **Control-plane removal is refused** — see [Control-plane nodes are not removed](#control-plane-nodes-are-not-removed).
- Inventory regeneration is deliberately deferred until *after* `remove-node.yml` succeeds, so Kubespray always gets a node it can still reach.
- **Workload visibility before you confirm** — lists real (non-DaemonSet, non-static-pod) workloads running on the node(s) about to be removed, so a model or service that's only running there doesn't get evicted as a surprise.
- **PodDisruptionBudget guard** — refuses the removal if any pod on the node is currently covered by a PDB with `disruptionsAllowed: 0`; eviction would be rejected by the API server no matter how many times `remove-node.yml` retries.
- **Preflight simulation (capacity / spread / local-data / last-capable-node)** — see below.
- **Last-worker warning** — flags when the removal would leave zero worker nodes, and separately reports whether the control-plane node currently has the `NoSchedule` taint (so you know whether that actually means "nothing schedulable anywhere" or just "back to single-node"). A warning, not a block — dropping to zero workers is a valid, already-supported topology when it's intentional. (A control-plane node without the taint is also a valid remaining placement target for the preflight simulation above — it isn't excluded just for being control-plane, only for taints the evicted pods don't tolerate.)
- **Balloon policy cleanup** — if CPU pinning (`nri_cpu_balloons`) is installed, `remove-node` deletes the removed node's `node.<NAME>` BalloonsPolicy once the node is gone, so a node later added under the same name starts from its own freshly generated policy instead of inheriting one written for the old hardware. (`add-node` applies the new node's per-node policy itself — see step 3 above.)
- **Post-remove health verdict** — once the node is gone, runs the same checks as `status --health` (node Readiness, no crash-looping/image-pull-failing/unschedulable pods) and fails the `remove-node` command on a problem that was not there before the removal. Advisory at that point, not a rollback: the node has already been reset and dropped from the cluster, so recovering means `add-node`, not an automatic revert.

### Preflight simulation (capacity / spread / local-data / last-capable-node)

Before `confirm()`, `roles/kubernetes/files/simulate_node_removal.py` simulates where every evicted pod on the node(s) being removed would have to land, and refuses outright — no flag skips this — when:

| Check | Refused when | Example |
|---|---|---|
| **Capacity** | the remaining nodes able to take these pods have too little free allocatable CPU/memory/GPU/hugepages for what they request | A model fills the only two workers; removing one leaves the other short on CPU for everything that would land there |
| **Spread** | a required pod anti-affinity or `topologySpreadConstraint` (`whenUnsatisfiable: DoNotSchedule`) can't be satisfied on the remaining nodes | Two replicas required on different hosts, only one host left |
| **Local data** | the pod uses a volume bound to that specific node (PV `local`/node-affinity, or a known local-path provisioner) | A `local-path` or similar node-pinned PVC — the data does not move with the pod |
| **Last capable node** | the pod's `nodeSelector`/required `nodeAffinity`/taints, or an extended resource it requests (e.g. a GPU), isn't satisfiable by any remaining node | The only GPU-labeled node while a GPU workload is running there |

This is a heuristic, not a scheduler simulation — it models the common cases above closely enough to catch them, not every possible scheduling predicate. Capacity is checked pod by pod: each evicted pod is placed on one specific remaining node it is allowed to run on, and that node's free capacity is reduced before the next pod is placed. The hardest pods to place go first (fewest eligible nodes, then largest request), and each goes on the eligible node it fills most tightly. So a pod whose only eligible node is full is refused even when other nodes have room it could never use. `--dry-run` prints the resulting plan: the target node for every pod it placed. It is a greedy placement, not an exact solver, so it can occasionally refuse a removal that a different placement order would have fit. A false refusal costs a retry; a false pass costs an outage.

For the same reason, if the simulation can't run at all (an API error, or the script itself failing), `remove-node` refuses with the error instead of treating it as "nothing found".

## If an action fails

Neither action rolls back. What was done stays done; re-running is the recovery path.

| Fails during | State | Recovery |
|---|---|---|
| `add-node` pre-flight | Nothing installed on the node | Fix the cause, re-run `add-node` |
| `add-node` `scale.yml` | Packages/containerd/kubelet partly installed | Re-run `add-node` (`scale.yml` is safe to repeat) |
| `add-node` post phase | Node joined, still tainted `add-node-pending` — nothing schedules there | Re-run `add-node`; it resumes the post phase |
| `remove-node` checks | Nothing changed | Fix the cause, re-run |
| `remove-node` drain | Node left cordoned | Re-run `remove-node`, or `kubectl uncordon <node>` to keep it |
| `remove-node` reset / post phase | Node partly reset, or removed but still in the inventory | Re-run `remove-node` (`--node-lost` if unreachable) |

Errors marked `...ignoring` in Kubespray's reset output are expected — for example `crictl` failing to stop pods once the node can no longer reach the API server, or `/run/calico` not being deletable because it holds a cgroup mount. They don't fail the run.

## Related Docs

| If you want to… | Go to |
|---|---|
| Deploy across multiple nodes in the first place | [Multi-Node & BYO Cluster](../deploy/topologies.md) |
| Understand the `workload-class` labels re-applied to new nodes | [Node Topology & Workload Placement](node_topology.md) |
| Look up every `global_config.yaml` / `nodes.yaml` field | [Configuration Reference](configuration.md) |

## External References

- [Kubespray — Adding/replacing a node](https://github.com/kubernetes-sigs/kubespray/blob/v2.31.0/docs/operations/nodes.md) — upstream docs for `scale.yml` and `remove-node.yml`, including the first-control-plane-node limitation this doc summarizes
