# NRI CPU Balloons — Quick Guide

[← Docs Index](../README.md)

A practical, copy-paste guide for enabling the NRI CPU-balloons policy
and deploying models that use it.

On a multi-socket server, threads that drift across memory domains pay for every remote memory access, and CPU inference is memory-bound enough for that to show up as lower tokens per second. This policy pins model servers to a fixed set of cores on one NUMA node, so the cores an LLM is given stay its own.

> [!NOTE]
> This guide applies when `kubernetes_cpu_policy: nri-balloons` is set. With the default policy the components below are not deployed, and models are scheduled without pinning.

---

## 1. What it does

The NRI (Node Resource Interface) balloons policy works in two stages:

1. **Declare CPU pools.** The role generates a `BalloonsPolicy` per node
   that defines a set of *balloon types* — per-NUMA + per-TP pools
   (`vllm-balloon-tp1`, `-tp2`, `-tp4`, plus cross-package combos) sized to
   that node's own NUMA topology — plus one elastic `reserved` pool.
2. **Pin at runtime.** When a container starts, the plugin assigns it to a
   matching balloon and pins its processes to that balloon's cpuset, so
   vLLM gets stable, predictable, NUMA-local CPU.

A model pod's CPU **request must fit inside one of the model balloon types**,
otherwise the pod will not be scheduled onto a balloon. All non-model pods
(including everything in `kube-system`) are routed to the `reserved` pool,
which is **elastic** — it inflates on demand by borrowing free cores rather
than staying fixed to its seed cpuset.

### 1.1 Worked example — a 2-NUMA node

On a node with 2 NUMA domains the role generates:

- `vllm-balloon-tp1` — a single-NUMA balloon; one instance is created per
  NUMA **on demand** (a TP=1 model lands entirely on one NUMA).
- `vllm-balloon-tp2` — a balloon that **spans both NUMA** domains (a TP=2
  model is split across the two NUMA nodes).
- `vllm-balloon-tp4` is **not** generated, because TP=4 needs ≥4 NUMA
  domains. (TP=8 likewise needs ≥8 NUMA.)

### 1.2 Why balloons help — two levels of isolation

Balloons isolate model workloads on two axes:

- **NUMA-level:** each balloon's cpuset is kept within one NUMA domain (or,
  for multi-NUMA TP balloons, a fixed set of whole NUMA domains), so memory
  stays local to the cores running the model.
- **Physical-core-level:** model balloon types set `hideHyperthreads: true`,
  so the balloon reserves **both** SMT siblings of each physical core but
  runs work on only one — the sibling is held idle. This removes
  hyperthread contention between co-located workloads.

Verified live: a TP=1 model pod was pinned to cpuset `132-147` (16 distinct
physical cores) while their siblings `304-319` were held idle. Note this
applies only to the `vllm-balloon-tp*` types, not to the `reserved` pool.

---

## 2. Enable the policy

Edit `global_config.yaml`:

```yaml
# CPU policy — set to "nri-balloons" to enable (this is the default).
# Alternatives: "kubelet-static", "best-effort"
kubernetes_cpu_policy: "nri-balloons"

# Optional: pin reserved CPUs to a fixed cpuset (e.g. "0-3" or "0,1,32,33").
# Leave empty to size automatically from `nri_deployment_profile` below.
# Only set an explicit cpuset on homogeneous clusters.
nri_reserved_cpu_list: ""

# Sizing profile for the reserved-CPU calculator.
# One of: siblings | minimal (8) | standard (12) | observability (16) | full (20)
nri_deployment_profile: "siblings"
```

Install / re-apply the role only (no full cluster re-run):

```bash
./es_auto_installer.sh install nri_cpu_balloons
```

Verify:

```bash
kubectl -n kube-system get ds nri-resource-policy-balloons
kubectl -n kube-system get balloonspolicy
# Expect: one `default` plus one `node.<NAME>` per worker node.
```

---

## 3. Reserved CPU sizing

Reserved CPUs are sized from `nri_deployment_profile`:

| Profile         | Total logical CPUs reserved |
| --------------- | --------------------------- |
| `siblings`      | All HT siblings on each NUMA (default) |
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
explicit Linux cpuset string.

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
# every non-llm-inference container should be a SUBSET of the reserved list
kubectl -n kube-system logs ds/nri-resource-policy-balloons \
    | grep "assigning container" | grep -v llm-inference
```

If system pods start CPU-throttling (look for `container_cpu_cfs_throttled_seconds_total`
climbing on `kube-system` / `cert-manager` / `metallb` pods), give the
reserved balloon more headroom by editing the per-node `BalloonsPolicy`:

```bash
kubectl -n kube-system edit balloonspolicy node.<NODE>
```

```yaml
spec:
  balloonTypes:
    - name: reserved
      minBalloons: 2          # allocate 2 reserved balloons up front
                              # (each lands on its own NUMA)
      # OR widen the reserved pool itself:
      # availableResources is set globally via nri_reserved_cpu_list /
      # nri_deployment_profile — bump the profile (e.g. standard → full)
      # and re-run `./es_auto_installer.sh install nri_cpu_balloons` to
      # rebuild the CRs.
```

Re-check after the edit:

```bash
kubectl -n kube-system get balloonspolicy node.<NODE> -o yaml \
    | grep -A2 "name: reserved"
```

To **fully disable** NRI (uninstall the plugin):

```bash
./es_auto_installer.sh teardown nri_cpu_balloons
# then in global_config.yaml:
#   nri_cpu_balloons_enabled: false
```

---

## 4. Models available

Defined in your environment's model catalog (`env/<name>/models.yaml`, seeded by `init`):

| Name                    | HF ID                              | CPU | TP  | Notes                  |
| ----------------------- | ---------------------------------- | --- | --- | ---------------------- |
| `llama3-8b-awq`         | (AWQ-quantised Llama-3 8B)         | 16  | 1   |                        |
| `llama-3-2-3b-instruct` | `meta-llama/Llama-3.2-3B-Instruct` | 8   | 1   | gated — needs HF_TOKEN |
| `qwen-3-1-1-7b`         | `Qwen/Qwen3-1.7B`                  | 16  | 1   |                        |
| `llama-3-2-1b-instruct` | `meta-llama/Llama-3.2-1B-Instruct` | 16  | 2   | gated — needs HF_TOKEN |

All entries are `LLMInferenceService` kind.

To list at any time:

```bash
./model-manager list
```

---

## 5. Deploy a model

```bash
# Open model — no token needed
./model-manager deploy qwen-3-1-1-7b --wait

# Gated model — needs Hugging Face token
export HF_TOKEN=hf_xxxxxxxxxxxx
./model-manager deploy llama-3-2-3b-instruct --wait
```

On deploy the `model-manager` will:

1. Provision storage (PVC).
2. **Run NRI pre-flight** (TP ≤ NUMA nodes, effective CPU ≤ per-NUMA × TP,
   effective CPU ≤ free balloon capacity). Fails fast **before** download.
3. Download weights to PVC (skipped with `--skip-download`).
4. Apply the `LLMInferenceService` manifest with the right balloon
   annotation.
5. Wait for Ready (`--wait`).

Check placement:

```bash
POD=$(kubectl -n llm-inference get pod \
    -l kserve.io/component=workload \
    -l serving.kserve.io/inferenceservice=qwen-3-1-1-7b \
    -o name | head -1)

# Which balloon was picked
kubectl -n llm-inference get $POD -o jsonpath=\
'{.metadata.annotations.balloon\.balloons\.resource-policy\.nri\.io/container\.main}{"\n"}'
# Expect: vllm-balloon-tp1  (for TP=1 models in advanced mode)

# Which physical CPUs the container is pinned to
kubectl -n llm-inference exec $POD -c main -- \
    cat /proc/self/status | grep Cpus_allowed_list
```

Undeploy:

```bash
./model-manager undeploy qwen-3-1-1-7b
```

---

## 6. Useful environment overrides

All variables below are **optional** — defaults work out of the box.

| Variable                  | Default / behaviour                                                                                       |
| ------------------------- | --------------------------------------------------------------------------------------------------------- |
| `MM_NRI_PREFLIGHT=warn`   | Pre-flight failures become warnings (deploy proceeds). Default: hard fail.                                |
| `MM_NRI_SKIP_PREFLIGHT=1` | Skip pre-flight entirely (no log lines). Default: run pre-flight.                                         |
| `MM_NRI_BALLOON_NAME=…`   | Hard-pin one deploy to a specific balloon (e.g. `vllm-balloon-tp4`). Default: auto-pick by TP.            |
| `HF_TOKEN=hf_…`           | Required only for gated Hugging Face models (Llama, etc.).                                                |

Per-model override in `models.yaml`:

```yaml
- name: my-model
  model_id: ...
  cpu: 16
  nri_balloon: vllm-balloon-tp2     # top-level scalar, or
  raw:
    nri_balloon: vllm-balloon-tp2   # nested form — both work
```

---

## 7. Troubleshooting (quick)

| Symptom                                                                 | Check                                                                                         |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Deploy aborts with `NRI balloons pre-flight failed`                     | Lower `cpu:` / `tp`, or set `MM_NRI_PREFLIGHT=warn` to override                              |
| Pod stuck `Pending` with `Insufficient cpu`                             | Balloons exhausted — undeploy something or shrink the model                                  |
| Workload landed in `reserved` instead of `vllm-balloon-tp*`             | Pod missing the `balloon.balloons.resource-policy.nri.io` annotation                          |
| System pods CPU-throttling (kube-system / cert-manager / metallb / etc.) | Reserved balloon is under-sized — bump `nri_deployment_profile` (e.g. `standard` → `full`) or set `minBalloons: 2` on the `reserved` type (see §3.1) |
| Need to see which balloon each container got                            | `kubectl -n kube-system logs ds/nri-resource-policy-balloons \| grep "assigning container"` |

---

## 8. Limitations

Known constraints of the current NRI balloons integration. Most are by
design; a few are roadmap items.

### Parallelism: TP-only NUMA awareness

- **Only tensor parallelism (TP) is NUMA-aware.** Balloon types are generated
  per-TP (`vllm-balloon-tp1/-tp2/-tp4/-tp8`) and a model is pinned to a NUMA
  layout chosen from its TP size.
- **Data parallelism (DP) and pipeline parallelism (PP) are *not*
  NUMA-aware.** There are no DP/PP-specific balloon types. A model that uses
  DP replicas or PP stages still lands in a single TP-sized balloon — the
  individual replicas/stages are **not** placed on separate NUMA domains, so
  you do not get per-replica / per-stage NUMA isolation.
- **TP must map to a generated balloon and be ≤ the node's NUMA count.**
  Supported sizes are `1, 2, 4, 8`; arbitrary values (e.g. TP=3, TP=6) are not
  generated and cannot be NUMA-isolated.

### Static, manual reserved-CPU sizing

- **Reservation is not dynamic.** The reserved pool is sized from an
  **explicitly set** `nri_deployment_profile` (or an explicit
  `nri_reserved_cpu_list`). The role does **not** auto-detect which components
  (Keycloak, APISIX, observability stack, Ceph, etc.) are actually installed
  and size the reservation accordingly.
- **Component changes need a manual update.** If you add or remove
  infrastructure components, you must change `nri_deployment_profile` (or the
  cpuset) and **re-run** `./es_auto_installer.sh install nri_cpu_balloons` to
  rebuild the CRs. The per-component breakdown in
  `roles/nri_cpu_balloons/vars/reserved_cpu_config.yml` is **informational**
  — it documents the assumed footprint of each profile, it is not measured
  from the live cluster.

### Reserved pool is elastic, not a hard guarantee

- The `reserved` balloon **inflates on demand** and can borrow free cores
  (including cores outside the seed list). It is **not** a hard cap, so under
  heavy system-pod load it can consume cores that would otherwise be available
  to model balloons. There is no guarantee that model cores stay untouched if
  the node is oversubscribed — plan headroom with the profile.

### Reinstalling with models already deployed

- **Re-running the role while models are running can reserve cores those
  models are already using.** If you reinstall `nri_cpu_balloons` with a
  **larger** `nri_deployment_profile` (e.g. `standard` → `full`), the
  recomputed reserved pool may claim cores currently pinned to live model
  balloons, causing contention or re-pinning churn.
- **Recommended order:** scale **down** all models first, reinstall the
  `nri_cpu_balloons` component with the new profile, then scale the models
  back **up** so they are placed against the updated reserved/model layout:

  ```bash
  # 1. scale down every deployed model
  for m in $(./model-manager list -o name); do ./model-manager undeploy "$m"; done

  # 2. reinstall with the new profile (set nri_deployment_profile first)
  ./es_auto_installer.sh install nri_cpu_balloons

  # 3. redeploy the models
  ./model-manager deploy <model> --wait
  ```

### Other constraints

- **Node-local only.** NRI balloons never span nodes; a single model instance
  cannot be split across two nodes. "Cluster capacity" is per-node capacity
  plus Kubernetes scheduling.
- **Hyperthreads are doubled.** With `hideHyperthreads: true`, a model's
  effective CPU footprint is **2× its `cpu:` request** (the physical core plus
  its idle SMT sibling). Size requests with this in mind.
- **Explicit cpuset override is homogeneous-only.** `nri_reserved_cpu_list` is
  applied as a fixed cpuset **only** when every node has an identical CPU
  layout. On heterogeneous fleets it is ignored in favour of `count` mode to
  avoid reserving CPU IDs a node does not have.
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
| Deploy, undeploy, or list models with `model-manager` | [Deploy an LLM](../deploy/deploy_models.md) |
| Separate platform pods from inference pods at the node level | [Node Topology & Workload Placement](node_topology.md) |
| See how NUMA-aware CPU placement fits the broader architecture | [Architecture & Design Document](../reference/architecture.md) |

---
