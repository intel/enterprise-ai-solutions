# Multi-Node & BYO Cluster

[← Docs Index](../README.md)

How to deploy on several machines, or on a Kubernetes cluster you already run.

There are two paths, and both end with the same platform:

- **Multi-node:** you list your machines, and the installer builds one Kubernetes cluster across them with [Kubespray](../glossary.md) (the open-source Kubernetes installer it uses).
- **Bring your own cluster (BYO):** you point the installer at an existing cluster. It skips building Kubernetes and installs only the platform and the layers above it.

The stack itself is the same either way. What changes is who builds the cluster, and where data is stored.

> [!IMPORTANT]
> A multi-node cluster needs **shared storage** that several nodes can mount at once (ReadWriteMany): `storage_backend: nfs`, `ceph` or `netapp-trident`, or `custom` with a ReadWriteMany class. The default, `local-path`, keeps each volume on one node, so the other nodes could not read the model files. On more than one node, `install` stops with an error if `storage_backend` is `local-path`. See [Storage](../customize/configuration.md#storage).

---

## Multi-node deployment

### Prerequisites

Check these on every machine that will join the cluster:

- [ ] The installer host can SSH to it with a key, without a password prompt.
- [ ] The SSH user can run `sudo` without a password.
- [ ] Its clock is synchronized (NTP or chrony). Clock skew breaks etcd (the cluster database) and TLS certificates.
- [ ] The installer host can reach it over the network.

You then describe the machines in one of two ways: [Option A](#option-a--configure-via-nodesyaml-simple) (recommended) or [Option B](#option-b--configure-via-inventoryhostsyaml-advanced).

---

### Step 1 — Create the environment

```bash
./toolkit_manager.sh configure                 # once per machine: installs Python, yq, kubectl and helm (uses sudo)
./toolkit_manager.sh init inference --env prod # creates env/prod/ and clones the inference repo into ext/
```

When it succeeds, `env/prod/` contains `global_config.yaml` and `nodes.yaml`, among others. Skip `configure` if those tools are already installed.

---

### Step 2 — Set global_config.yaml for multi-node

Open `env/prod/global_config.yaml` and set these before you install:

```yaml
# REQUIRED: shared storage, so every node can read the model files
storage_backend: nfs

# The base domain for every service URL (grafana.<domain>, keycloak.<domain>, ...)
base_domain_name: "inteltoolkits.ai"

# Behind a corporate proxy? Uncomment and fill in:
# http_proxy:  "http://proxy.example.com:8080"
# https_proxy: "http://proxy.example.com:8080"
# no_proxy: "localhost,127.0.0.1,.svc,.cluster.local,.monitoring,10.233.0.0/18,10.233.64.0/18,<node-subnet>"
```

With `nfs`, the installer turns the **first control-plane node** into an NFS server that exports `/data/nfs`. You don't need any external storage. To use another NFS server or path, set `nfs_server` and `nfs_path` in `nodes.yaml`.

For replicated block storage instead, use `ceph`. See [Configuration](../customize/configuration.md#storage).

---

### Step 3 — Configure nodes

Choose one option.

#### Option A — Configure via `nodes.yaml` (simple)

You list the machines in `env/prod/nodes.yaml`. The installer writes the Kubespray inventory (`env/prod/inventory/hosts.yaml`) from it on each install.

```yaml
# Control-plane node(s): 1 for a basic cluster, 3 for high availability (HA).
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: master1

# Worker node(s), optional. Leave this out and the control-plane node also runs workloads.
nodes_workers:
  - ip: 10.0.1.20
    hostname: worker1
  - ip: 10.0.1.21
    hostname: worker2

# SSH login used for every node
nodes_ssh_user: "ubuntu"
nodes_ssh_key: "/home/ubuntu/.ssh/cluster_key"   # the file must be chmod 600
```

- `hostname` must be lower case: Kubernetes node names are lower case.
- A single node can use its own login: add `ssh_user:` or `ssh_key:` under that node's entry.
- To have a control-plane node run workloads as well as workers, list it under `nodes_workers` too, with exactly the same entry.

#### Option B — Configure via `inventory/hosts.yaml` (advanced)

Use this for full control: jump hosts, per-node SSH settings, or custom Kubespray groups. Leave `nodes_control_plane` in `nodes.yaml` empty, so the installer uses your file as it is. Then edit `env/prod/inventory/hosts.yaml`:

```yaml
all:
  hosts:
    master1:
      ansible_host: 10.0.1.10
    worker1:
      ansible_host: 10.0.1.20
    worker2:
      ansible_host: 10.0.1.21
  children:
    kube_control_plane:
      hosts:
        master1: {}
    kube_node:
      hosts:
        worker1: {}
        worker2: {}
    etcd:
      children:
        kube_control_plane:
    k8s_cluster:
      children:
        kube_control_plane:
        kube_node:
      vars:
        ansible_user: ubuntu
        ansible_ssh_private_key_file: ~/.ssh/cluster_key
        ansible_become: true
        ansible_connection: ssh   # required: without it, the node checks run on the installer host
    calico_rr:
      hosts: {}
```

To let `master1` run workloads too, add it under `kube_node`.

**Behind a jump host?** Add this line to the `vars` block:

```yaml
        ansible_ssh_common_args: '-o ProxyJump=jumpuser@10.0.0.5:22'
```

---

### Step 4 — Install

```bash
./toolkit_manager.sh install inference --env prod
```

What happens:

1. The installer checks that it can SSH to every node.
2. Kubespray builds Kubernetes across the nodes. This shows its own live output and takes the longest.
3. The installer deploys the platform layer, then the inference layer.

To check the result:

```bash
export KUBECONFIG=$(pwd)/env/prod/kubeconfig.yaml
kubectl get nodes   # every node should show STATUS Ready
```

---

## HA — 3 control-plane nodes

List three control-plane nodes in `env/prod/nodes.yaml`:

```yaml
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: master1
  - ip: 10.0.1.11
    hostname: master2
  - ip: 10.0.1.12
    hostname: master3

nodes_workers:
  - ip: 10.0.1.20
    hostname: worker1
  - ip: 10.0.1.21
    hostname: worker2
```

- **etcd** runs on all three control-plane nodes. It keeps working while any 2 of the 3 are up.
- **MetalLB** (which gives the gateway its external IP address) picks its own address pool when `metallb_ip_range` is not set. With more than one control-plane node, that pool is `.240`–`.250` in the first node's subnet (here `10.0.1.240-10.0.1.250`). Make sure nothing else on your network uses those addresses, or set `metallb_ip_range` yourself in `nodes.yaml`. See [IP allocation](networking.md#ip-allocation-strategy).

---

## Workload placement

When `node_topology_enabled: true` (the default) and the cluster has worker nodes, the installer labels the nodes and asks the scheduler to place pods by those labels:

| Workload type | Prefers | Node label |
|---|---|---|
| Platform components (Keycloak, PostgreSQL, Envoy Gateway, KServe controller, ...) | Control-plane nodes | `workload-class=platform` |
| Inference workloads (model-serving pods) | Worker nodes | `workload-class=inference` |

These are **preferences, not rules**: when a preferred node is full, pods run elsewhere. No taints are added. On a single node nothing is labelled. See [Node Topology](../customize/node_topology.md) for details.

---

## Bring your own Kubernetes (BYO)

Install the platform on a cluster you already manage. The installer skips Kubespray completely.

1. Put the cluster's kubeconfig file on the installer host.
2. In `env/<name>/global_config.yaml`, point to it:

   ```yaml
   existing_kubernetes: "/absolute/path/to/kubeconfig"
   ```

   The installer stops with an error if the file does not exist or can't be read.

3. Turn off platform components your cluster already provides, for example:

   ```yaml
   cert_manager_enabled: false     # you already run cert-manager (the platform still needs one)
   metallb_enabled: false          # a cloud load balancer gives Services their IPs
   istio_enabled: false            # no Istio service mesh
   observability_enabled: false    # no Prometheus/Grafana stack
   ```

   Even with these left on, the installer skips its own cert-manager install when a cert-manager Helm release or the OpenShift operator is already there, and its MetalLB install when the cluster already has a LoadBalancer provider.

4. Install as usual:

   ```bash
   ./toolkit_manager.sh install inference --env <name>
   ```

> **BYO with Headlamp:** the installer does not change your API server. See
> [Headlamp and Platform Sign-in](headlamp.md#bring-your-own-kubernetes-byo) for the
> one-time API server step, or the mode that needs none.

> **BYO node topology:** the installer does not label nodes on a BYO cluster. To get the placement above, label them yourself:
>
> ```bash
> kubectl label nodes master1 master2 workload-class=platform
> kubectl label nodes worker1 worker2 worker3 workload-class=inference
> ```
>
> `teardown` removes these labels again.

---

## Teardown

```bash
./toolkit_manager.sh teardown infrastructure --env prod
```

The installer asks you to confirm (add `--force` to skip the prompt), then:

1. Tears down every layer installed on top of the cluster (solutions, inference, platform).
2. Runs Kubespray `reset.yml` on every node. **This destroys the Kubernetes cluster.**
3. Deletes `env/prod/kubeconfig.yaml`.

Your configuration under `env/prod/` is kept, so `install` can rebuild the same cluster.

On a BYO cluster, `teardown` never resets the cluster: it only removes what the installer put on it.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Understand the single-node path this guide branches from | [Getting Started](../quickstart/quickstart.md) |
| Add or remove worker nodes on a running cluster | [Node Scaling](../customize/node_scaling.md) |
| See exactly how platform vs. inference pods get placed on your new nodes | [Node Topology & Workload Placement](../customize/node_topology.md) |
| Set `storage_backend`, TLS, or other multi-node-required settings | [Configuration Reference](../customize/configuration.md#storage) |
| Trace how external traffic reaches these nodes | [Network Architecture](networking.md) |
