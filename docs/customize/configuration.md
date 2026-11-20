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
| `routing_mode` | `subdomain` | `subdomain`: one host name per service. `path`: one host name, services under paths such as `<domain>/grafana` — see [Routing mode](#routing-mode) |
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
to `env/<name>/global_config.yaml`. Its active lines are the defaults. Its `# key: value` lines are
optional settings: remove the `# ` to use one. The file has eight sections, and the tables below
follow them.

### 1. Kubernetes

| Setting | Default | What it does |
|---|---|---|
| `kubernetes_cluster_name` | `cluster.local` | The cluster's internal DNS suffix |
| `kubernetes_kube_proxy_mode` | `nftables` | How Kubernetes routes service traffic: `nftables`, `iptables` or `ipvs` |
| `existing_kubernetes` | unset | Path to the kubeconfig of a cluster you already run. The installer then skips building one — see [BYO cluster](#byo-cluster) |

### 2. Networking and proxy

| Setting | Default | What it does |
|---|---|---|
| `kubernetes_service_cidr` | `10.233.0.0/18` | Address range for Kubernetes services |
| `kubernetes_cluster_cidr` | `10.233.64.0/18` | Address range for pods |
| `metallb_ip_range` | detected | Range of IPs (`a-b`) MetalLB hands out to load balancers. Leave unset to detect one |
| `http_proxy`, `https_proxy`, `no_proxy` | unset | Corporate proxy — see [Proxy](#proxy) |

### 3. Domain and access

| Setting | Default | What it does |
|---|---|---|
| `base_domain_name` | `inteltoolkits.ai` | The domain every service address is built from |
| `routing_mode` | `subdomain` | `subdomain` or `path` — see [Routing mode](#routing-mode) |
| `gateway_landing_path` | Headlamp when it is on | Path mode only: where the bare domain sends you, for example `/grafana/` |
| `gateway_tls_mode` | `selfsigned` | `selfsigned` or `custom` — see [TLS](#tls) |
| `gateway_tls_cert_file`, `gateway_tls_key_file` | unset | `custom` only: your certificate and key |
| `gateway_tls_ca_file` | unset | `custom` only, optional: the private CA that signed your certificate |
| `gateway_request_timeout` | `600s` | How long the gateway waits for an answer. Envoy's own default (15 s) is too short for model responses |

### 4. Components

Keycloak and the gateway always install. These switch the rest on or off.

| Setting | Default | What it does |
|---|---|---|
| `istio_enabled` | `true` | Istio ambient service mesh: encrypted (mTLS) traffic between services |
| `observability_enabled` | `true` | Grafana, Prometheus, Loki and Tempo |
| `observability_prometheus_retention` | `30d` | How long Prometheus keeps metrics |
| `observability_prometheus_storage_size` | `50Gi` | Prometheus disk size |
| `metrics_server_enabled` | `true` | Node and pod CPU/memory figures (`kubectl top`, Headlamp). Skipped if the cluster already has one |
| `headlamp_enabled` | `false` | Headlamp, the Kubernetes web UI, with Keycloak sign-in — see [Headlamp](../deploy/headlamp.md) |
| `keda_enabled` | `false` | KEDA event-driven autoscaling (on OpenShift: the Custom Metrics Autoscaler) |
| `velero_enabled` | `false` | Velero backup and restore. Needs snapshot-capable storage (`nfs` or `ceph`) |
| `kata_containers_enabled` | `false` | Kata Containers microVM runtime for the Agent Toolkit sandbox — see [Kata Containers](#kata-containers) |
| `kata_runtime` | `kata-fc` | `kata-fc` (Firecracker), `kata-qemu` or `both` |

### 5. Storage

| Setting | Default | What it does |
|---|---|---|
| `storage_backend` | `local-path` | `local-path`, `nfs`, `ceph`, `netapp-trident` or `custom` — see [Storage](#storage) |
| `storage_custom_storageclass` | unset | `custom` only, required: the StorageClass to use |
| `storage_custom_rwx` | `true` | `custom` only: `false` for a class that cannot be shared by several nodes |
| `object_store_backend` | `seaweedfs` | S3 store for Loki, Tempo and Langfuse: `seaweedfs` or `external` — see [Object store](#object-store) |
| `seaweedfs_volume_storage_size` | `10Gi` | Size of the SeaweedFS data volume. Fixed after the first install |

### 6. Sign-in

Keycloak signs people in. LiteLLM and Langfuse are set in `config.inference.yaml`.

| Setting | Default | What it does |
|---|---|---|
| `auth_type` | `jwt` (Agent Toolkit: `litellm_vkey`) | API authentication — see [API authentication](#api-authentication). Leave it commented here: a value in this file overrides every layer's choice |
| `keycloak_platform_bootstrap_admin_mfa` | `false` | `true`: the platform admin must set up a one-time-password app at first sign-in |
| `headlamp_byo_apiserver_trusted` | `false` | Your own cluster's API server already trusts the platform realm — see [Headlamp](../deploy/headlamp.md) |

### 7. Advanced

Most installs keep these.

| Setting | Default | What it does |
|---|---|---|
| `node_topology_enabled` | `true` | Prefer control-plane nodes for platform pods and worker nodes for models (a preference, not a rule) — see [Node Topology](node_topology.md) |
| `enforce_pss` | `true` | Pod Security labels on every namespace the installer creates — see [Namespace Security Labels](../reference/labels.md) |
| `kubernetes_cpu_policy` | `nri-balloons` | How model pods get CPU cores — see [CPU policy](#cpu-policy) |
| `kubernetes_accelerator` | `cpu` | `cpu`: tune the kubelet's CPU/NUMA settings for Xeon. CPU pinning is turned off for any other value |
| `rack_scale_enabled` | `false` | Rack-scale fabric; needs `env/<name>/rack-profile.yaml` — see [Rack-scale deployment](../rack-scale-deployment.md) |
| `envoy_gateway_upgrade` | `false` | `true`: install Envoy Gateway 1.9.3 instead of 1.7.5 (and upgrade an older one in place). Never downgrades |

### 8. Hardware and platform specific

| Settings | When | See |
|---|---|---|
| `rook_ceph_devices`, `rook_ceph_teardown_wipe_devices` | `storage_backend: ceph` | [Storage](#storage) |
| `ontap_*`, `gateway_s3_subdomain` | `storage_backend: netapp-trident` | [NetApp ONTAP and Trident](../deploy/netapp_ontap.md) |
| `cluster_distribution`, `openshift_prereq_*` | OpenShift | [Deploy on OpenShift](../deploy/openshift.md) |

---

# Detailed reference

## Routing mode

How services are published on the domain.

| `routing_mode` | Addresses | When to use |
|---|---|---|
| `subdomain` (default) | One host name per service: `grafana.<domain>`, `keycloak.<domain>`, `inference.<domain>` | You can create a wildcard DNS entry (`*.<domain>`) |
| `path` | One host name, services under paths: `<domain>/grafana`, `<domain>/keycloak`, `<domain>/headlamp`, `<domain>/inference` | You have a single DNS name, or want every UI on one browser origin |

In `path` mode, Langfuse and the vLLM Semantic Router dashboard still use their own subdomains.
`gateway_landing_path` sets where the bare domain sends you (Headlamp by default, when it is on).

---

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
