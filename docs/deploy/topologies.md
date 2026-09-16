# Multi-Node & BYO Cluster

[← Docs index](../README.md)

Use this page when one machine is not enough, or when you already run Kubernetes
and only want the installer to deploy the foundation and toolkits onto that
cluster. Single-node localhost is covered in [Getting Started](../quickstart/quickstart.md).

---

## Multi-node deployment

### Prerequisites

Before starting, ensure the following are in place on every target node:

- [ ] Passwordless SSH from the installer host to every target node
- [ ] Passwordless sudo on every target node
- [ ] Clocks synchronized (NTP/chrony), skew causes etcd and TLS failures
- [ ] All nodes reachable on the same network from the installer host

Once the above are met, configure the node list and SSH credentials using [Option A](#option-a--configure-via-nodesyaml-simple) (recommended) or [Option B](#option-b--configure-via-inventoryhostsyaml-advanced).

---

### Step 1: Create the environment

```bash
./es_auto_installer.sh configure
./es_auto_installer.sh init prod
```

---

### Step 2: Set global_config.yaml for multi-node

Open `env/prod/global_config.yaml` and set the following before installing:

```yaml
# REQUIRED: shared storage so model weights are accessible from every node
storage_backend: nfs

# Set your base domain (applies to all service URLs)
base_domain_name: "solutions.ai"

# If behind a corporate proxy, uncomment and fill in:
# http_proxy:  "http://proxy.example.com:8080"
# https_proxy: "http://proxy.example.com:8080"
# no_proxy: "localhost,127.0.0.1,10.233.0.0/18,10.233.64.0/18,.svc,.cluster.local,<node-subnet>"
```

`nfs` automatically provisions an NFS server on the first control-plane node at `/data/nfs`. No external storage is needed.

> The installer aborts if `storage_backend: local-path` is detected on a multi-node cluster.

For redundant block storage instead, use `ceph`, see [Configuration](../customize/configuration.md#storage).

---

### Step 3: Configure nodes

Choose one option:

#### Option A: Configure via `nodes.yaml` (simple)

The installer auto-generates the Kubespray inventory from `env/prod/nodes.yaml`. Edit it:

```yaml
# Control-plane node(s): use 1 for basic clusters, 3 for HA.
nodes_control_plane:
  - ip: 10.0.1.10
    hostname: master1

# Worker node(s): optional. Omit to make the control-plane schedulable for workloads.
nodes_workers:
  - ip: 10.0.1.20
    hostname: worker1
  - ip: 10.0.1.21
    hostname: worker2

# SSH credentials applied to ALL nodes
nodes_ssh_user: "ubuntu"
nodes_ssh_key: "/home/ubuntu/.ssh/cluster_key"   # chmod 600
```

#### Option B: Configure via `inventory/hosts.yaml` (advanced)

For fine-grained control, jump hosts, per-node SSH settings, custom Kubespray groups. Edit `env/prod/inventory/hosts.yaml`:

```yaml
all:
  hosts:
    master1:
      ansible_host: 10.0.1.10
      ip: 10.0.1.10
    worker1:
      ansible_host: 10.0.1.20
      ip: 10.0.1.20
    worker2:
      ansible_host: 10.0.1.21
      ip: 10.0.1.21
  children:
    kube_control_plane:
      hosts:
        master1:
    kube_node:
      hosts:
        master1:    # include master if it should also run workloads
        worker1:
        worker2:
    etcd:
      hosts:
        master1:
    k8s_cluster:
      children:
        kube_control_plane:
        kube_node:
  vars:
    ansible_user: ubuntu
    ansible_ssh_private_key_file: ~/.ssh/cluster_key
```

**Behind a jump host?** Add to the `vars` block:

```yaml
  vars:
    ansible_ssh_common_args: '-o ProxyJump=jumpuser@10.0.0.5:22'
```

---

### Step 4: Install

```bash
./es_auto_installer.sh install --all --env prod
```

The installer SSHes into the nodes, provisions Kubernetes across them via Kubespray, then deploys platform and inference layers.

After install:

```bash
export KUBECONFIG=$(pwd)/env/prod/kubeconfig.yaml
kubectl get nodes   # should show all nodes Ready
```

---

## HA: 3 control-plane nodes

In `env/prod/nodes.yaml`:

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

With 3 control-plane nodes, MetalLB uses all three IPs as its pool so the gateway can failover between them. etcd achieves quorum with any 2 of 3 nodes alive.

---

## Workload placement

When `node_topology_enabled: true` (default), the installer labels nodes and applies soft affinity rules:

| Workload type | Prefers | Label |
|---|---|---|
| Platform components (Keycloak, PostgreSQL, Envoy, KServe controller, etc.) | Control-plane nodes | `workload-class=platform` |
| Inference workloads (vLLM pods) | Worker nodes | `workload-class=inference` |

These are **soft preferences**, the scheduler can override if resources are constrained. No taints are applied. See [Node Topology](../customize/node_topology.md) for details.

---

## Bring your own Kubernetes (BYO)

Deploy the platform on a cluster you already manage. All Kubespray phases are skipped.

In `env/<name>/global_config.yaml`:

```yaml
existing_kubernetes: "/absolute/path/to/kubeconfig"
```

Disable any platform components you already run:

```yaml
cert_manager_enabled: false     # already have cert-manager
metallb_enabled: false          # using a cloud load balancer
istio_enabled: false            # bring your own service mesh
observability_enabled: false    # skip Prometheus/Grafana stack
```

Then install as normal:

```bash
./es_auto_installer.sh install --all --env myenv
```

> **BYO node topology:** node labels (`workload-class=platform/inference`) are not applied automatically. Apply them manually if you want workload placement:
>
> ```bash
> kubectl label nodes master1 master2 workload-class=platform
> kubectl label nodes worker1 worker2 worker3 workload-class=inference
> ```

---

## Teardown

```bash
./es_auto_installer.sh teardown --all --env prod
```

Runs Kubespray `reset.yml` across all nodes. Configuration files under `env/prod/` are preserved. The kubeconfig is removed.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Understand the single-node path this guide branches from | [Getting Started](../quickstart/quickstart.md) |
| See exactly how platform vs. inference pods get placed on your new nodes | [Node Topology & Workload Placement](../customize/node_topology.md) |
| Set `storage_backend`, TLS, or other multi-node-required settings | [Configuration Reference](../customize/configuration.md#storage) |
| Trace how external traffic reaches these nodes | [Network Architecture](networking.md) |
