# Configuration Reference

[← Docs Index](../README.md)

## What Does global_config.yaml Control?

A Toolkit Manager deployment is described by **one file**:

```
env/<name>/global_config.yaml
```

Domain names, TLS, storage, compute, and which platform components get installed all come from
this file. Settings that belong to one solution layer, such as API authentication (`auth_type`)
and the LiteLLM/Langfuse add-ons, live next to it in `env/<name>/config.<layer>.yaml`. You never
write these files from scratch: `init` creates both with working defaults.

### How it fits together

```bash
./toolkit_manager.sh init inference    # 1. creates env/local/global_config.yaml with defaults
vi env/local/global_config.yaml        # 2. (optional) change what you need
./toolkit_manager.sh install inference # 3. reads the file and deploys
```

Step 2 is optional. The defaults deploy a complete, working stack on a single machine — that is
exactly what the [Quick Start](../../README.md#quick-start) runs.

Every later command (`install <component>`, `teardown`, …) reads the same file, so it stays the
source of truth for the life of the environment. To change something later, edit the file and run
`install` again.

### One file per environment

Each environment is a folder under `env/` with its own config, kubeconfig and logs. Environments
are fully independent:

```
env/local/global_config.yaml     # your laptop / dev box
env/prod/global_config.yaml      # the real cluster
```

`--env <name>` selects which one a command acts on, and defaults to `local`.

> [!WARNING]
> Install and teardown are environment-scoped. If you installed with `--env prod`,
> you must tear down with `--env prod` — a teardown against another environment
> does not touch the one you meant.

### Do I Need to Change global_config.yaml Before My First Deploy?

Not for a single-machine trial. The defaults assume: one node (this machine), the
`inteltoolkits.ai` domain, a self-signed TLS certificate, Keycloak sign-in, storage on the node's
own disk, and inference on Intel® Xeon® CPUs.

Edit the file when one of these is true:

| If you… | Change |
|---|---|
| Have more than one node | `storage_backend` → `nfs` or `ceph` (**required** — the installer stops otherwise) |
| Own a real domain | `base_domain_name` |
| Have real certificates | `gateway_tls_mode` → `custom` |
| Want every service under one host name instead of one per service | `routing_mode` → `path` |
| Already run Kubernetes | `existing_kubernetes` → path to your kubeconfig |
| Are behind a corporate proxy | `http_proxy` / `https_proxy` / `no_proxy` |
| Already run cert-manager, Istio, Prometheus… | `<component>_enabled` → `false` |

### The settings most people touch

| Setting | Default | What it does |
|---|---|---|
| `base_domain_name` | `inteltoolkits.ai` | Every service address derives from this: `grafana.<domain>`, `keycloak.<domain>`, `inference.<domain>` |
| `routing_mode` | `subdomain` | `subdomain`: one host name per service. `path`: one host name, services under paths such as `<domain>/grafana` — see [Networking](#networking) |
| `auth_type` | `jwt` | The credential API clients send: a Keycloak token (`jwt`) or a LiteLLM key (`litellm_vkey`). Set in `env/<name>/config.inference.yaml` — see [API authentication](#api-authentication) |
| `storage_backend` | `local-path` | Where persistent data lives — see [Storage](#storage) |
| `gateway_tls_mode` | `selfsigned` | Generate a certificate, or bring your own — see [TLS](#tls) |
| `observability_enabled` | `true` | Deploy the monitoring stack: Grafana, Prometheus, Loki, Tempo |
| `velero_enabled` | `false` | Deploy the backup mechanism (Velero). Needs `storage_backend: nfs` or `ceph`; `run_backup.sh` backs up and restores a layer with it (see [Upgrade a Layer](../deploy/upgrade.md#backup-and-restore)) |

Everything else is safe to leave alone until you have a reason to change it.

---

## Changing values

There are two ways.

**1. Edit the file** — `env/<name>/global_config.yaml` (or the layer's
`env/<name>/config.<layer>.yaml`) — and run the install again:

```yaml
storage_backend: "nfs"
```

```bash
./toolkit_manager.sh install inference --env local
```

**2. Override for one run**, without editing anything:

```bash
./toolkit_manager.sh install inference --env local -- -e routing_mode=path
```

Anything after the bare `--` goes straight to Ansible, so any setting on this page can be
overridden that way. The override applies to that run only; the next run uses the file again.

**Which value wins** (highest first):

```
-- -e on the command line
  > env/<name>/nodes.yaml
  > env/<name>/global_config.yaml
  > env/<name>/config.<layer>.yaml   (a layer's own file wins over the files of layers below it)
  > the components' built-in defaults (roles/<component>/defaults/main.yaml)
```

A value in `global_config.yaml` therefore overrides the same key in a layer's config file. That is
why the template leaves `auth_type` commented out.

---

## Full template

`init` copies [`configs/defaults/global_config.yaml`](../../configs/defaults/global_config.yaml)
to `env/<name>/global_config.yaml`. Its active lines are the defaults; its `# key: value`
lines are optional settings (remove the `# ` to use one). It has eight sections:

| Section | What it sets |
|---|---|
| 1. Kubernetes | cluster name, kube-proxy mode, `existing_kubernetes` (your own cluster) |
| 2. Networking and proxy | service and pod CIDRs, `metallb_ip_range`, `http_proxy` / `https_proxy` / `no_proxy` |
| 3. Domain and access | `base_domain_name`, `routing_mode`, `gateway_landing_path`, TLS (`gateway_tls_mode`, custom certificate files), `gateway_request_timeout` |
| 4. Components | `istio_enabled`, `observability_enabled` (and Prometheus retention and size), `metrics_server_enabled`, `headlamp_enabled`, `keda_enabled`, `velero_enabled`, `kata_containers_enabled`, `kata_runtime` |
| 5. Storage | `storage_backend` and its `custom` settings, `object_store_backend`, `seaweedfs_volume_storage_size` |
| 6. Sign-in | `auth_type` (API auth: `jwt` by default, `litellm_vkey` with the Agent Toolkit; set here only to force it for every layer), platform-admin MFA, `headlamp_byo_apiserver_trusted` |
| 7. Advanced | `node_topology_enabled`, `enforce_pss`, CPU policy, `kubernetes_accelerator`, `rack_scale_enabled`, `envoy_gateway_upgrade` |
| 8. Hardware and platform specific | Ceph devices, NetApp ONTAP, OpenShift checks |

Keycloak and the gateway always install; section 4 switches the rest on or off. LiteLLM and
Langfuse are set in `config.inference.yaml`, not here.

### Field summary

Every setting in the template, in the template's order. Defaults are what you get when the line
is left as it is (or commented out).

| Field | What it controls |
|---|---|
| `kubernetes_cluster_name` | The cluster's internal DNS suffix. Default `cluster.local` |
| `kubernetes_kube_proxy_mode` | How Kubernetes routes service traffic: `nftables` (default), `iptables` or `ipvs` |
| `existing_kubernetes` | Path to the kubeconfig of a cluster you already run; the installer then skips building one. Unset by default. `KUBECONFIG` is not supported: the installer uses this path, or `env/<name>/kubeconfig.yaml` for a managed cluster — see [BYO cluster](#byo-cluster) |
| `kubernetes_service_cidr` | Address range for Kubernetes services. Default `10.233.0.0/18` |
| `kubernetes_cluster_cidr` | Address range for pods. Default `10.233.64.0/18` |
| `metallb_ip_range` | Range of IPs (`a-b`) MetalLB hands out to load balancers. Leave unset to have one detected |
| `http_proxy`, `https_proxy`, `no_proxy` | Corporate proxy. Unset by default — see [Proxy](#proxy) |
| `base_domain_name` | All service URLs derive from this (e.g. `grafana.<domain>`, `litellm.<domain>`). Default `inteltoolkits.ai` |
| `routing_mode` | `subdomain` (default: one host name per service) or `path` (one host name, services under paths) — see [Networking](#networking) |
| `gateway_landing_path` | Path mode only: where the bare domain sends you, for example `/grafana/`. Default: Headlamp, when it is on |
| `gateway_tls_mode` | How TLS certs are provisioned: `selfsigned` (default, an auto-generated CA) or `custom` (bring your own) — see [TLS](#tls) |
| `gateway_tls_cert_file`, `gateway_tls_key_file` | `custom` only: your certificate and key |
| `gateway_tls_ca_file` | `custom` only, optional: the private CA that signed your certificate |
| `gateway_request_timeout` | Max time the gateway waits for a model response before timing out. Default `600s` (Envoy's own 15 s is too short for model responses) |
| `istio_enabled` | Istio ambient service mesh: encrypted (mTLS) traffic between services. Default `true` |
| `observability_enabled` | Whether the full monitoring stack (Prometheus/Grafana/Loki/Tempo) is deployed. Default `true` |
| `observability_prometheus_retention`, `observability_prometheus_storage_size` | How long Prometheus keeps metrics (default `30d`) and its disk size (default `50Gi`) |
| `metrics_server_enabled` | Node and pod CPU/memory figures (`kubectl top`, Headlamp). Default `true`; skipped if the cluster already has one |
| `headlamp_enabled` | Headlamp, the Kubernetes web UI, with Keycloak sign-in. Default `false` — see [Headlamp](../deploy/headlamp.md) |
| `keda_enabled` | KEDA event-driven autoscaling (on OpenShift: the Custom Metrics Autoscaler). Default `false` |
| `velero_enabled` | Whether the backup mechanism (Velero) is deployed. Default `false`; needs snapshot-capable storage (`nfs` or `ceph`) |
| `kata_containers_enabled`, `kata_runtime` | Kata Containers microVM runtime for the Agent Toolkit sandbox. Default `false`; runtime `kata-fc` (Firecracker, default), `kata-qemu` or `both` — see [Kata Containers](#kata-containers) |
| `storage_backend` | Where persistent data lives: `local-path` (default), `nfs`, `ceph`, `netapp-trident` or `custom`. Must be shared storage for multi-node — see [Storage](#storage) |
| `storage_custom_storageclass`, `storage_custom_rwx` | `custom` only: the StorageClass to use (required), and `false` for a class several nodes cannot share (default `true`) |
| `object_store_backend` | S3 store for Loki, Tempo and Langfuse data: `seaweedfs` (default) or `external` — see [Object store](#object-store) |
| `seaweedfs_volume_storage_size` | Size of the SeaweedFS data volume. Default `10Gi`; fixed after the first install |
| `auth_type` | API authentication: `jwt` (default; Agent Toolkit: `litellm_vkey`). Leave it commented here: a value in this file overrides every layer's choice — see [API authentication](#api-authentication) |
| `keycloak_platform_bootstrap_admin_mfa` | `true`: the platform admin must set up a one-time-password app at first sign-in. Default `false` |
| `headlamp_byo_apiserver_trusted` | Your own cluster's API server already trusts the platform realm. Default `false` — see [Headlamp](../deploy/headlamp.md) |
| `node_topology_enabled` | Prefer control-plane nodes for platform pods and worker nodes for models (a preference, not a rule). Default `true` — see [Node Topology](node_topology.md) |
| `enforce_pss` | Pod Security labels on every namespace the installer creates. Default `true` — see [Namespace Security Labels](../reference/labels.md) |
| `kubernetes_cpu_policy` | CPU pinning strategy for inference workloads. Default `nri-balloons` — see [CPU policy](#cpu-policy) |
| `kubernetes_accelerator` | Compute target for inference workloads. Default `cpu` (Intel® Xeon®: tune the kubelet's CPU/NUMA settings); CPU pinning is turned off for any other value |
| `rack_scale_enabled` | Rack-scale fabric; needs `env/<name>/rack-profile.yaml`. Default `false` — see [Rack-scale deployment](../rack-scale-deployment.md) |
| `envoy_gateway_upgrade` | `true`: install Envoy Gateway 1.9.3 instead of 1.7.5 (and upgrade an older one in place). Default `false`; never downgrades |
| `rook_ceph_devices`, `rook_ceph_teardown_wipe_devices` | `storage_backend: ceph` only — see [Storage](#storage) |
| `ontap_*`, `gateway_s3_subdomain` | `storage_backend: netapp-trident` only — see [NetApp ONTAP and Trident](../deploy/netapp_ontap.md) |
| `cluster_distribution`, `openshift_prereq_*` | OpenShift only — see [Deploy on OpenShift](../deploy/openshift.md) |

---

# Detailed reference

## TLS

The gateway terminates all external HTTPS traffic. There is no plain-HTTP mode.

| `gateway_tls_mode` | What happens |
|---|---|
| `selfsigned` (default) | cert-manager creates a private root CA and signs a certificate for `<base_domain_name>` and `*.<base_domain_name>` with it. After install the CA is saved to `env/<name>/logs/ai-solutions-ca.crt`. Import it once (below) to stop browser warnings |
| `custom` | You supply the certificate and key |

**Use your own certificate:**

```yaml
gateway_tls_mode: "custom"
gateway_tls_cert_file: "/path/to/cert.pem"   # must cover <base_domain> and *.<base_domain>
gateway_tls_key_file:  "/path/to/key.pem"
# gateway_tls_ca_file: "/path/to/ca.pem"     # optional: the private CA that signed it
```

**Import the self-signed CA:**

| Where | How |
|---|---|
| Firefox | Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import |
| Chrome / Edge | Settings → Privacy → Security → Manage certificates → Authorities → Import |
| Linux, system-wide | `sudo cp env/<name>/logs/ai-solutions-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates` |
| macOS | `sudo security add-trusted-cert -d -k /Library/Keychains/System.keychain env/<name>/logs/ai-solutions-ca.crt` |

---

## API authentication

Keycloak is always deployed: people sign in to the web UIs through it in both modes. `auth_type`,
set in `env/<name>/config.inference.yaml`, only chooses the credential that programs (API
clients) send with each request.

| `auth_type` | What's deployed | Request path | Best for |
|---|---|---|---|
| `jwt` (default) | Keycloak and its inference configuration; LiteLLM and Langfuse only with `litellm_enabled: true` | The gateway checks the Keycloak token → model | Single sign-on, per-client tokens |
| `litellm_vkey` | The above, plus LiteLLM, Langfuse and Valkey | Client → `litellm.<domain>` (LiteLLM checks the virtual key) → model | Many teams, per-key budgets |

The Intel® Agent Toolkit defaults to `litellm_vkey`.

Every gateway route needs a Keycloak token unless it has its own sign-in. Routes that sign people
in themselves (the Grafana, Langfuse and Keycloak logins, the LiteLLM API) bring their own policy.
Langfuse is on whenever LiteLLM is, unless `langfuse_enabled: false`. The `litellm_*` and
`langfuse_*` settings also live in `config.inference.yaml`.

> [!NOTE]
> The old `auth_provider` setting is deprecated. Preflight maps `keycloak` →
> `jwt` and `litellm` → `litellm_vkey` with a warning. A value that conflicts
> with `auth_type` fails preflight.

---

## Storage

| `storage_backend` | What it is | When to use |
|---|---|---|
| `local-path` (default) | Storage on each node's own disk (one node at a time can use a volume) | **Single node only** |
| `nfs` | The installer sets up an NFS server on the first control-plane node, at `/data/nfs`, and shares it with every node through the `csi-driver-nfs` driver (so volumes can be snapshotted). The StorageClass is called `nfs-client` | **Multi-node**: every node needs to read the model weights. Also needed for `velero_enabled` |
| `ceph` | Replicated storage across nodes with Rook-Ceph, on raw disks you name | Multi-node with spare disks, when data must survive a node failure |
| `netapp-trident` | NetApp ONTAP storage through Trident | You have an ONTAP array — see [NetApp ONTAP and Trident](../deploy/netapp_ontap.md) |
| `custom` | Nothing is installed; your existing StorageClass is used as it is | Your cluster already runs its own storage driver |

> **Multi-node warning:** the installer stops if `storage_backend: local-path` is set on a
> multi-node cluster. Change it to `nfs` (or `ceph`) before you install.

**Custom (bring your own storage driver):**

```yaml
storage_backend: custom
storage_custom_storageclass: "my-csi-sc"   # required
storage_custom_rwx: true                   # false = class that only one node can use at a time
```

Every component creates its volumes without naming a StorageClass, so your class must be the
cluster's **only** default class:

```console
kubectl annotate storageclass my-csi-sc storageclass.kubernetes.io/is-default-class=true --overwrite
```

The installer checks the class exists and is the only default, and then leaves it alone. It never
creates, changes or deletes it, and `teardown` does not touch it.

`storage_custom_rwx` is the one thing the installer cannot detect: whether several nodes can mount
a volume at once (ReadWriteMany) depends on the driver, not on the StorageClass. It sets the access
mode of the shared `model-store` volume, which the download job and every model pod mount at the
same time. Leave it `true` for a shared file system (NFS, CephFS, ontap-nas, EFS, Azure Files). Set
it `false` only for a single-node-access class (such as EBS, `ceph-block` or hostPath); that limits
you to one node, and the installer stops if it finds more. If it is wrong, `model-store` stays
`Pending`; since the access mode cannot be changed, you then have to delete that volume claim by
hand.

**Ceph settings:**

```yaml
storage_backend: ceph
rook_ceph_devices:            # raw disks per node; the nodes listed run Ceph
  node-1: [/dev/nvme1n1]
  node-2: [/dev/nvme1n1]
# rook_ceph_teardown_wipe_devices: false   # true: teardown wipes these disks
```

On rack-scale (`rack_scale_enabled: true`), leave `rook_ceph_devices` out: the disks come only from
`storage.nodes` in `rack-profile.yaml`, and install and teardown stop if `rook_ceph_devices` lists
different ones (see [Rack-scale deployment](../rack-scale-deployment.md)).

---

## Proxy

Uncomment and set these if this machine or the cluster nodes reach the internet through a
corporate proxy.

```yaml
http_proxy:  "http://proxy.example.com:8080"
https_proxy: "http://proxy.example.com:8080"
# no_proxy must list the Kubernetes ranges and your node subnets.
# Find the node subnets with: ip route | grep -v default | awk '{print $1}' | grep '/'
no_proxy: "localhost,127.0.0.1,.svc,.cluster.local,.monitoring,10.233.0.0/18,10.233.64.0/18,<node-subnet>"
```

> **`kubectl` and proxy:** if `HTTP(S)_PROXY` is set in your shell, `kubectl` may fail with
> `Forbidden` errors because its calls go through the proxy. Add the cluster ranges to `no_proxy`
> in your shell:
> ```bash
> export no_proxy="localhost,127.0.0.1,10.233.0.0/18,10.233.64.0/18,.svc,.cluster.local"
> export NO_PROXY="$no_proxy"
> ```
>
> Also list the API server address itself (the `server:` in your kubeconfig, for example the
> control-plane node's IP). The kubeconfig on control-plane nodes points at the node's IP, and some
> clients (Python `httpx`, for example) ignore ranges such as `10.0.0.0/8` in `no_proxy`.

---

## CPU policy

`kubernetes_cpu_policy` is the single switch for CPU pinning (giving a model pod its own CPU
cores). Everything else — the NRI plugin, the kubelet settings, the model manifests — follows
from it.

| Value | What it does | When to use |
|---|---|---|
| `nri-balloons` (default) | Installs the NRI balloons plugin: NUMA-aware pinning with hyperthread isolation. Pods keep normal (Burstable) QoS | Recommended — best performance on multi-socket Xeon nodes |
| `kubelet-static` | Configures the kubelet's static CPU manager (`full-pcpus-only`). Pods become Guaranteed QoS. No NRI plugin | Clusters that cannot run NRI |
| `best-effort` | No pinning: the scheduler places threads | Development, or small clusters |

Any other value stops preflight. Pinning only applies with `kubernetes_accelerator: cpu`. See
[NRI CPU Balloons](nri_cpu_balloons.md) for per-node balloon sizing.

---

## Disable components

Set `<component>_enabled: false` to skip something you already run:

```yaml
cert_manager_enabled: false     # you already have cert-manager
metallb_enabled: false          # you use a cloud or external load balancer
istio_enabled: false            # you bring your own service mesh
observability_enabled: false    # no Prometheus/Grafana/Loki stack
postgresql_enabled: false       # you use an external PostgreSQL
metrics_server_enabled: false   # you already run metrics-server
```

Run `./toolkit_manager.sh show` to see every component.

---

## Networking

| Setting | Default | What it does |
|---|---|---|
| `gateway_request_timeout` | `600s` | How long Envoy waits for an answer — raised from its 15 s default for long model responses |
| `kubernetes_cluster_name` | `cluster.local` | The cluster's DNS suffix |
| `kubernetes_kube_proxy_mode` | `nftables` | `nftables`, `iptables` or `ipvs` |
| `node_topology_enabled` | `true` | Platform pods prefer control-plane nodes, model pods prefer workers. See [Node Topology](node_topology.md) |
| `routing_mode` | `subdomain` | How services are published on the domain: `subdomain` or `path` (see below) |

`routing_mode` decides the service addresses:

| `routing_mode` | Addresses | When to use |
|---|---|---|
| `subdomain` (default) | One host name per service: `grafana.<domain>`, `keycloak.<domain>`, `inference.<domain>` | You can create a wildcard DNS entry (`*.<domain>`) |
| `path` | One host name, services under paths: `<domain>/grafana`, `<domain>/keycloak`, `<domain>/headlamp`, `<domain>/inference` | You have a single DNS name, or want every UI on one browser origin |

In `path` mode, Langfuse and the vLLM Semantic Router dashboard still use their own subdomains.
`gateway_landing_path` sets where the bare domain sends you (Headlamp by default, when it is on).

---

## Node preflight

Before Kubespray builds a cluster, the installer runs read-only checks on every node. The checks
are listed in [Prerequisites](../quickstart/prerequisites.md#node-checks-before-install).

| Setting | Default | What it does |
|---|---|---|
| `kubernetes_node_preflight` | `true` | `false` turns all the checks off |
| `kubernetes_node_preflight_skip` | `[]` | Check IDs to skip, for example `[N8]` for nodes that download from a mirror instead of the internet |

A failed check stops the install and lists the IDs you could skip.

---

## Object store

The S3-compatible store for Loki logs, Tempo traces and Langfuse files. The platform deploys its
own SeaweedFS for it (release `telemetry` in the `monitoring` namespace), separate from the
SeaweedFS that the Intel® Chatbot Toolkit installs.

```yaml
object_store_backend: "seaweedfs"          # seaweedfs | external
seaweedfs_volume_storage_size: "10Gi"      # data volume; fixed after the first install
```

With `external`, nothing is installed and Loki and Tempo use your store. Set its address and
keys in `global_config.yaml`; the install stops if one is empty:

```yaml
object_store_backend: "external"
object_store_endpoint: "https://s3.example.com"
object_store_loki_access_key: "..."
object_store_loki_secret_key: "..."
object_store_tempo_access_key: "..."
object_store_tempo_secret_key: "..."
```

---

## Admin passwords

The Keycloak and Grafana admin passwords are **generated** (24 random letters and digits) on the
first install and stored in Kubernetes Secrets. Installing again keeps the existing password; it
is never rotated for you.

To read them after install (the username is `admin` for both):

```bash
# Grafana
kubectl get secret -n monitoring grafana-admin-credentials \
  -o jsonpath='{.data.password}' | base64 -d; echo

# Keycloak
kubectl get secret -n keycloak keycloak-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

Grafana's admin signs in on Grafana's own login form; see
[Observability](../deploy/observability.md#sign-in).

To choose the passwords yourself, set them **before the first install** (once a Secret exists, its
password is kept), either in `global_config.yaml`:

```yaml
keycloak_admin_password: "your-password"
observability_grafana_admin_password: "your-password"
```

or for that install only:

```bash
./toolkit_manager.sh install inference -- \
  -e keycloak_admin_password=your-password \
  -e observability_grafana_admin_password=your-password
```

A password on the command line lands in your shell history; the file is the safer choice (keep it
readable only by you: `chmod 600`).

---

## BYO cluster

To install onto a Kubernetes cluster you already run ("bring your own"), point the installer at
its kubeconfig:

```yaml
existing_kubernetes: "/absolute/path/to/kubeconfig"   # skips every Kubespray step
```

The installer then never builds, resets or scales the cluster: `teardown infrastructure` leaves it
running, and `add-node` / `remove-node` are refused. A `KUBECONFIG` set in your shell is not used;
this setting is the only way to name your cluster. See
[Multi-Node & BYO Cluster](../deploy/topologies.md).

---

## Kata Containers

Opt-in microVM runtime for the Agent Sandbox of the Intel® Agent Toolkit: each sandbox runs as a
small virtual machine with its own kernel, instead of a pod sharing the host's kernel. Set the two
Kata switches in `env/<name>/global_config.yaml`, where `init` puts them (a value there wins over
`config.agent-toolkit.yaml`).

| Setting | Default | What it does |
|---|---|---|
| `kata_containers_enabled` | `false` | Install Kata Containers (the `kata_containers` infrastructure component) with the upstream kata-deploy chart |
| `kata_runtime` | `kata-fc` | `kata-fc` (Firecracker), `kata-qemu` or `both`: which runtimes kata-deploy sets up |
| `kubernetes_registry_enabled` | `false` | Kubespray's in-cluster image registry (`kube-system`, port 5000). `config.agent-toolkit.yaml` turns it on; it only applies when Kubespray builds the cluster, so run `init agent-toolkit` before the first install |

Before anything is installed, every node in the inventory is checked and prepared:

- `/dev/kvm` (Intel VT-x on bare metal, or nested virtualisation on a VM) and
  `/dev/vhost-vsock`. The installer loads the `kvm_intel` and `vhost_vsock` modules itself.
- `kata-fc` and `both` also need containerd's devmapper snapshotter: the installer creates a
  sparse loopback thin-pool under `/var/lib/containerd/devmapper` and registers it in the
  containerd config. On CPUs with AMX, Firecracker needs kernel 5.17 or later.

A missing prerequisite stops the install with its cause and every node's result. Nodes without
`/dev/kvm` are left out, as long as one node has it. Kata is not supported on a BYO cluster
(`existing_kubernetes`) or on OpenShift: the host preparation runs over SSH on the inventory nodes,
so the install stops there. Teardown removes the kata-deploy release and the node label; the
thin-pool unit, the containerd config entry and the `vhost_vsock` module entry stay on the nodes.

---

## Version pinning

Each component's version is pinned in its defaults file, `roles/<component>/defaults/main.yaml`
(for layers from another repository, in that repository's roles). A release is tested with those
versions, so normally you change versions by moving to a newer Toolkit Manager release, not by
editing them.

The one version switch in `global_config.yaml` is `envoy_gateway_upgrade`: `true` moves Envoy
Gateway from 1.7.5 to 1.9.3 (and the matching Gateway API and KServe release lines), and never
downgrades.

For a test, you can override a pinned version for one run, for example:

```bash
./toolkit_manager.sh install cert_manager --env local -- -e <variable>=<version>
```

Look up the exact variable name in that component's `defaults/main.yaml` first. An untested
version may not work with the rest of the stack.

---

## Per-Layer Solution Configs

Next to `global_config.yaml`, each solution layer has its own config file, created by
`init <layer>`:

- **`env/<name>/config.inference.yaml`** — from the inference repository's `config.yaml`
- **`env/<name>/config.erag.yaml`** — from the chosen flavour's `config.yaml` (default: `chatqna`)
- **`env/<name>/config.agent-toolkit.yaml`** — from the Agent Toolkit repository's `config.yaml`

These files are **flat** lists of settings, passed to Ansible as they are (`-e @file`). A setting
that holds a nested block replaces the default block entirely; it is not merged with it. The
installer loads the layer files first (lower layers before higher ones), then
`global_config.yaml`, so a value in `global_config.yaml` wins over the same key in a layer's file.

Example: `config.erag.yaml` sets Chatbot Toolkit options such as `ui_enabled`, `edp_enabled` and
`vector_databases_enabled`. Setting the same key in `global_config.yaml` overrides it.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See every CLI command and flag that reads this file | [CLI Reference](cli.md) |
| Configure per-node CPU pinning (`kubernetes_cpu_policy: nri-balloons`) in depth | [NRI CPU Balloons](nri_cpu_balloons.md) |
| Set up soft affinity for platform vs. inference workloads (`node_topology_enabled`) | [Node Topology & Workload Placement](node_topology.md) |
| Go from a fresh clone to a running cluster using these settings | [Deployment Guide](../deploy/install_platform.md) |
