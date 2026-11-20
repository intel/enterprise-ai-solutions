# NRI CPU Balloons — Quick Guide

[← Docs Index](../README.md)

A practical, copy-paste guide to the NRI CPU-balloons policy: what it does, how to tune it, and how models use it.

On a multi-socket server, threads that drift across memory domains pay for every remote memory access, and CPU inference is memory-bound enough for that to show up as lower tokens per second. This policy pins model servers to a fixed set of cores on one NUMA node, so the cores an LLM is given stay its own.

Two terms used throughout:

- **NUMA node**: a group of CPU cores with the memory attached closest to them. A two-socket server usually has two or more.
- **Balloon**: a named set of CPU cores. A container placed in a balloon runs only on those cores.

> [!NOTE]
> `kubernetes_cpu_policy: nri-balloons` is the default on CPU clusters (`kubernetes_accelerator: cpu`). With `kubelet-static` or `best-effort`, or on a GPU cluster, the components below are not deployed and models are scheduled without balloon pinning.

---

## 1. What it does

The NRI (Node Resource Interface) balloons policy works in two stages:

1. **Declare CPU pools.** The role generates a `BalloonsPolicy` per node
   that defines a set of *balloon types* — one per tensor-parallel size
   (`model-balloon-tp1`, `-tp2`, `-tp4`, `-tp8`) sized to that node's own
   NUMA topology — plus one `reserved` pool for everything else.
2. **Pin at runtime.** When a container starts, the plugin assigns it to a
   matching balloon and pins its processes to that balloon's cpuset, so
   every model engine (vLLM, OpenVINO Model Server) gets stable, predictable,
   NUMA-local CPU.

*Tensor parallelism (TP)* splits one model across several NUMA nodes; `tp: 2` uses two. `model-manager` picks the balloon type from the model's TP, rounded up to 1, 2, 4 or 8.

A model pod's CPU **request must fit inside one of the model balloon types**,
otherwise the pod is not placed in a balloon. All non-model pods
(including everything in `kube-system`) are routed to the `reserved` pool,
which is **elastic** — it inflates on demand by borrowing free cores rather
than staying fixed to its seed cpuset.

### 1.1 Worked example — a 2-NUMA node

On a node with 2 NUMA domains the role generates:

- `model-balloon-tp1` — a single-NUMA balloon; one instance is created per
  NUMA **on demand** (a TP=1 model lands entirely on one NUMA).
- `model-balloon-tp2` — a balloon that **spans both NUMA** domains (a TP=2
  model is split across the two NUMA nodes).
- `model-balloon-tp4` is **not** generated, because TP=4 needs ≥4 NUMA
  domains. (TP=8 likewise needs ≥8 NUMA.)

### 1.2 Why balloons help — two levels of isolation

Balloons isolate model workloads on two axes:

- **NUMA-level:** each balloon's cpuset is kept within one NUMA domain (or,
  for multi-NUMA TP balloons, a fixed set of whole NUMA domains), so memory
  stays local to the cores running the model.
- **Physical-core-level** (with the default `siblings` profile): the second
  hyperthread of every physical core is put in the `reserved` pool, so a
  model balloon gets physical cores only. This removes hyperthread contention
  between co-located workloads. The other profiles reserve a fixed number of
  CPUs instead, and model balloons can then include hyperthreads.

Verified live: a TP=1 model pod requesting 8 CPUs was pinned to cpuset `0-7`
(8 physical cores) while their siblings `172-179` stayed in the `reserved`
pool.

---

## 2. Enable the policy

The policy is on by default. The settings, in `env/<name>/global_config.yaml`:

```yaml
# CPU policy: nri-balloons (default) | kubelet-static | best-effort
kubernetes_cpu_policy: "nri-balloons"

# Optional: pin reserved CPUs to a fixed cpuset (e.g. "0-3" or "0,1,32,33").
# Leave empty to size automatically from `nri_deployment_profile` below.
# Only set an explicit cpuset on homogeneous clusters.
nri_reserved_cpu_list: ""

# Sizing profile for the reserved-CPU calculator.
# One of: siblings | minimal (8) | standard (12) | observability (16) | full (20)
nri_deployment_profile: "siblings"
```

`nri_cpu_balloons` is a **platform-layer** component: `install platform` (and
`install inference`/`install erag`, which pull in the platform layer) installs or
re-applies it, and `teardown platform` removes it. `teardown inference` keeps
it, because every pod on the node is placed in a balloon, not only model pods.
Each of these installs re-applies the role, so a changed `nri_deployment_profile`
or `nri_reserved_cpu_list` takes effect on the next one, even an `install platform`
run for another reason. With models deployed, see
[Reinstalling with models already deployed](#reinstalling-with-models-already-deployed) first.

To install or re-apply the role only (no full layer run):

1. Run:

   ```bash
   ./toolkit_manager.sh install nri_cpu_balloons --env <name>
   ```

2. Verify the plugin and the policies:

   ```bash
   kubectl -n kube-system get ds nri-resource-policy-balloons
   kubectl -n kube-system get balloonspolicy
   ```

   Expect one `default` policy, plus one `node.<NAME>` for every node that takes workloads. A control-plane node with its `NoSchedule` taint gets none: the topology probe does not run there.

---

## 3. Reserved CPU sizing

Reserved CPUs are sized from `nri_deployment_profile`:

| Profile         | Total logical CPUs reserved |
| --------------- | --------------------------- |
| `siblings`      | All hyperthread siblings (default); `standard` if hyperthreading is off |
| `minimal`       | 8                           |
| `standard`      | 12                          |
| `observability` | 16                          |
| `full`          | 20                          |

The chosen total is spread evenly across the node's NUMA domains and
balanced between physical cores and their hyperthread siblings, so the
resulting cpuset stays NUMA-local.

These totals size the **infrastructure footprint** (control-plane, gateway,
observability, storage pods) — they are **not** a function of the machine's
core count. Pick a profile by which components the cluster runs, not by how
many cores the node has: e.g. `full` (20 logical ≈ 10 physical cores) only
reserves more because it assumes observability + storage are present, and it
still leaves the remaining cores free for model balloons.

Override sizing per cluster by setting `nri_reserved_cpu_list` to an
explicit Linux cpuset string. It is used only when every node has the same CPU layout; see [Other constraints](#other-constraints).

### 3.1 Reserved-balloon runtime behaviour

The reserved CPU list (e.g. `0,1,24,25,48,49,72,73,96,120,144,168` on a
4-NUMA node) is the reserved pool's **seed**, spread across all NUMA
domains. The reserved balloon is **elastic**: it starts on a subset of that
seed and **inflates on demand** to satisfy the combined CPU requests of the
pods assigned to it. When it needs more cores than the seed provides it
**borrows free cores from the node** — including cores outside the reserved
list — up to the node's full capacity.

The practical consequence is **headroom**, not throttling: an inflating
reserved balloon consumes cores that model balloons could otherwise use, so
the real cost of a growing system-pod footprint is fewer free cores left for
model workloads. (Per-pod CPU *limits* are enforced separately via CFS
quota and are independent of balloon size.)

With typical control-plane / system-pod load you will see every non-model
container sharing the reserved balloon's current cpuset (e.g. `72-73,168`).

Verify quickly:

```bash
# every container outside llm-inference should be a SUBSET of the reserved list
kubectl -n kube-system logs ds/nri-resource-policy-balloons \
    | grep "assigning container" | grep -v llm-inference
```

If system pods start CPU-throttling (look for `container_cpu_cfs_throttled_seconds_total`
climbing on `kube-system` / `cert-manager` / `metallb` pods), give the reserved pool more room:

1. **Lasting fix:** pick a bigger profile (e.g. `standard` → `full`) in `global_config.yaml`, then re-run `./toolkit_manager.sh install nri_cpu_balloons --env <name>`. With models running, follow [Reinstalling with models already deployed](#reinstalling-with-models-already-deployed).
2. **Quick test on one node:** edit its policy and allocate two reserved balloons up front (each lands on its own NUMA). The next install of the role overwrites this edit.

   ```bash
   kubectl -n kube-system edit balloonspolicy node.<NODE>
   ```

   ```yaml
   spec:
     balloonTypes:
       - name: reserved
         minBalloons: 2
   ```

   Re-check after the edit:

   ```bash
   kubectl -n kube-system get balloonspolicy node.<NODE> -o yaml \
       | grep -A2 "name: reserved"
   ```

To **turn NRI off**:

1. Remove the plugin while the policy is still `nri-balloons` (a disabled component is skipped by `teardown`):

   ```bash
   ./toolkit_manager.sh teardown nri_cpu_balloons --env <name>
   ```

2. Set `kubernetes_cpu_policy: "best-effort"` in `global_config.yaml`. (`nri_cpu_balloons_enabled` is derived from this setting; setting it by hand has no effect.)
3. Re-run `./toolkit_manager.sh install inference --env <name>` so `model-manager` gets the new policy (`MM_CPU_POLICY`), then redeploy the models.

---

## 4. Models available

The catalog is your environment's `env/<name>/models.yaml` (seeded by `init`). To see every model and whether it is deployed:

```bash
./model-manager list
```

Each entry sets the CPU cores it asks for (`cpu:`), and its TP size through `--tensor-parallel-size` in `args` (1 when absent). A small public model to start with is `qwen3-0-6b` (8 cores). Gated models (for example the Meta Llama ones) need a Hugging Face token in `HF_TOKEN`.

---

## 5. Deploy a model

```bash
# Open model — no token needed
./model-manager deploy qwen3-0-6b --wait

# Gated model — needs Hugging Face token
export HF_TOKEN=hf_xxxxxxxxxxxx
./model-manager deploy <gated-model> --wait
```

On deploy, `model-manager`:

1. Runs the **placement pre-flight**: under `nri-balloons`, it checks there are enough free physical cores on one node for `cpu` × TP × replicas, without using the reserved hyperthread siblings. It fails **before** anything is downloaded.
2. Downloads the weights to the `model-store` volume.
3. Applies the serving manifest with the balloon annotation (`balloon.balloons.resource-policy.nri.io/container.main: model-balloon-tp<N>`).
4. Waits until the model is Ready (`--wait`).

Check placement:

```bash
POD=$(kubectl -n llm-inference get pod \
    -l app.kubernetes.io/name=qwen3-0-6b -o name | head -1)

# Which balloon was picked
kubectl -n llm-inference get $POD -o jsonpath=\
'{.metadata.annotations.balloon\.balloons\.resource-policy\.nri\.io/container\.main}{"\n"}'
# Expect: model-balloon-tp1  (for a TP=1 model)

# Which CPUs the container is pinned to
kubectl -n llm-inference exec $POD -c main -- \
    grep Cpus_allowed_list /proc/self/status
```

For a cluster-wide view of pinning, NUMA layout and any two containers sharing a core:

```bash
./model-manager cpu-collisions-check
```

Undeploy:

```bash
./model-manager undeploy qwen3-0-6b
```

---

## 6. Useful environment overrides

All variables below are **optional** — defaults work out of the box.

| Variable                  | Default / behaviour                                                                                       |
| ------------------------- | --------------------------------------------------------------------------------------------------------- |
| `MM_PREFLIGHT=warn`       | Placement pre-flight failures become warnings (deploy proceeds). Default: hard fail. `MM_NRI_PREFLIGHT=warn` also works. |
| `MM_SKIP_PREFLIGHT=1`     | Skip the placement pre-flight entirely. Default: run it.                                                  |
| `MM_NRI_BALLOON_NAME=…`   | Hard-pin one deploy to a specific balloon type (e.g. `model-balloon-tp4`). Default: picked from the TP size. |
| `MM_CPU_POLICY=…`         | `nri-balloons` \| `kubelet-static` \| `best-effort`. Set by the install from `kubernetes_cpu_policy`; `--cpu-policy` on the command line wins. |
| `HF_TOKEN=hf_…`           | Required only for gated Hugging Face models (Llama, etc.).                                                |

To change a model's size for one deploy, use `--cpu N` and `--tp N` on `deploy`.

---

## 7. Troubleshooting (quick)

| Symptom                                                                 | Check                                                                                         |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Deploy aborts with `NRI pre-flight failed: not enough physical cores`  | Lower `--cpu` / `--replicas`, raise `--tp`, undeploy a model, or add a node. `MM_PREFLIGHT=warn` proceeds anyway |
| Pod stuck `Pending` with `Insufficient cpu`                             | Balloons exhausted — undeploy something or shrink the model                                  |
| Workload landed in `reserved` instead of `model-balloon-tp*`             | Pod missing the `balloon.balloons.resource-policy.nri.io/container.main` annotation          |
| System pods CPU-throttling (kube-system / cert-manager / metallb / etc.) | Reserved pool is under-sized — bump `nri_deployment_profile` (e.g. `standard` → `full`), see §3.1 |
| Need to see which balloon each container got                            | `kubectl -n kube-system logs ds/nri-resource-policy-balloons \| grep "assigning container"` |

---

## 8. Limitations

Known constraints of the current NRI balloons integration. Most are by
design; a few are roadmap items.

### Parallelism: TP-only NUMA awareness

- **Only tensor parallelism (TP) is NUMA-aware.** Balloon types are generated
  per-TP (`model-balloon-tp1/-tp2/-tp4/-tp8`) and a model is pinned to a NUMA
  layout chosen from its TP size.
- **Data parallelism (DP) and pipeline parallelism (PP) are *not*
  NUMA-aware.** There are no DP/PP-specific balloon types. A model that uses
  DP replicas or PP stages still lands in a single TP-sized balloon — the
  individual replicas/stages are **not** placed on separate NUMA domains, so
  you do not get per-replica / per-stage NUMA isolation.
- **TP sizes are 1, 2, 4 and 8, and at most the node's NUMA count.** Any other
  value is rounded up to the next size (TP=3 uses `model-balloon-tp4`), so it
  needs that many NUMA nodes.

### Static, manual reserved-CPU sizing

- **Reservation is not dynamic.** The reserved pool is sized from
  `nri_deployment_profile` (or an explicit `nri_reserved_cpu_list`). The role
  does **not** detect which components (Keycloak, observability stack, Ceph,
  etc.) are actually installed and size the reservation accordingly.
- **Component changes need a manual update.** If you add or remove
  infrastructure components, change `nri_deployment_profile` (or the
  cpuset) and **re-run** `./toolkit_manager.sh install nri_cpu_balloons` to
  rebuild the policies. The per-component breakdown in
  `roles/nri_cpu_balloons/vars/reserved_cpu_config.yml` is **informational**
  — it documents the assumed footprint of each profile, it is not measured
  from the live cluster.

### Reserved pool is elastic, not a hard guarantee

- The `reserved` balloon **inflates on demand** and can borrow free cores
  (including cores outside the seed list). It is **not** a hard cap, so under
  heavy system-pod load it can consume cores that would otherwise be available
  to model balloons. There is no guarantee that model cores stay untouched if
  the node is oversubscribed — plan headroom with the profile.

### Adding and removing nodes

- **`add-node`** creates the new node's own `node.<NAME>` BalloonsPolicy and
  `nri.intel.com/*` labels from its probed topology. The Helm release, the
  `default` policy and every existing node's policy and labels are left
  untouched, so running models are not re-pinned. The new node carries a
  `NoSchedule` taint until its policy exists, so its balloons agent never
  starts on `default`.
- **`remove-node`** deletes the removed node's `node.<NAME>` policy once the
  node is gone. See [Node Scaling](node_scaling.md).

### Reinstalling with models already deployed

- **Re-running the role while models are running can reserve cores those
  models are already using.** If you reinstall `nri_cpu_balloons` with a
  **larger** `nri_deployment_profile` (e.g. `standard` → `full`), the
  recomputed reserved pool may claim cores currently pinned to live model
  balloons, causing contention or re-pinning churn.
- **Recommended order:** undeploy the models first, reinstall the
  `nri_cpu_balloons` component with the new profile, then deploy the models
  again so they are placed against the updated layout:

  1. Note the deployed models (`./model-manager list --deployed`), then undeploy them all and wait for their pods to stop:

     ```bash
     ./model-manager undeploy all --wait
     ```

  2. Set the new `nri_deployment_profile` in `global_config.yaml`, then reinstall:

     ```bash
     ./toolkit_manager.sh install nri_cpu_balloons --env <name>
     ```

  3. Deploy each model again:

     ```bash
     ./model-manager deploy <model> --wait
     ```

### Other constraints

- **Node-local only.** NRI balloons never span nodes; a single model instance
  cannot be split across two nodes. "Cluster capacity" is per-node capacity
  plus Kubernetes scheduling.
- **A `cpu:` request is physical cores** (with the `siblings` profile). The siblings are reserved, so a
  model's `cpu:` request is the number of physical cores it is pinned to.
  model-manager places replicas on each node's real NUMA layout, published by
  this role as the `nri.intel.com/numa-cpus` node annotation.
- **Explicit cpuset override is homogeneous-only.** `nri_reserved_cpu_list` is
  applied as a fixed cpuset **only** when every node has an identical CPU
  layout. On heterogeneous fleets it is ignored and each node is sized from its own
  topology, to avoid reserving CPU IDs a node does not have.
- **Intel® Xeon® CPU inference.** The policy targets CPU-based serving on
  Intel® Xeon® processors with NUMA-aware core allocation.
- **Requires an NRI-enabled runtime.** The node's container runtime
  (containerd) must have NRI enabled; without it the plugin cannot pin
  containers.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Set `kubernetes_cpu_policy` and other cluster-wide settings this guide assumes | [Configuration Reference](configuration.md) |
| Deploy, undeploy, or list models with `model-manager` | [Deploy a Model](../deploy/deploy_models.md) |
| Separate platform pods from inference pods at the node level | [Node Topology & Workload Placement](node_topology.md) |
| See how NUMA-aware CPU placement fits the broader architecture | [Architecture & Design Document](../reference/architecture.md) |

---
