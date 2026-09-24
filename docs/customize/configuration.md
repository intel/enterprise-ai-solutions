# Configuration Reference

[← Docs Index](../README.md)

## What Does global_config.yaml Control?

An Intel® AI for Enterprise Solutions deployment is described by **one file**:

```
env/<name>/global_config.yaml
```

Domain names, TLS, authentication, storage, compute, and which components get
installed all come from this file. There is no other place you need to look, and
no YAML you have to write from scratch — `init` generates the file for you with
working defaults.

### How it fits together

```
./es_auto_installer.sh init inference  # 1. creates env/local/global_config.yaml with defaults
vi env/local/global_config.yaml        # 2. (optional) change what you need
./es_auto_installer.sh install inference # 3. reads the file and deploys
```

Step 2 is optional. The defaults deploy a complete, working stack on a single
node — that is exactly what the [Quick Start](../../README.md#quick-start) runs.

Every later command (`install <component>`, `teardown`, …) reads the same file,
so the file stays the source of truth for the life of the environment.

### One file per environment

Each environment is a directory under `env/` with its own config, kubeconfig, and
logs — they are fully independent:

```
env/local/global_config.yaml     # your laptop / dev box
env/prod/global_config.yaml      # the real cluster
```

`--env <name>` selects which one a command acts on, and defaults to `local`.

> [!WARNING]
> Install and teardown are environment-scoped. If you installed with `--env prod`,
> you must teardown with `--env prod` — running teardown against a different
> environment will not touch the one you meant.

### Do I Need to Change global_config.yaml Before My First Deploy?

Not for a single-node trial. The defaults assume: one node (localhost), the
`solutions.ai` domain, a self-signed TLS CA, Keycloak authentication, node-local
storage, and Intel® Xeon® CPU inference.

You need to edit the file when one of these is true:

| If you… | Change |
|---|---|
| Have more than one node | `storage_backend` → `nfs` or `ceph` (**required** — the installer aborts otherwise) |
| Own a real domain | `base_domain_name` |
| Have real certificates | `gateway_tls_mode` → `custom` |
| Already run Kubernetes | `existing_kubernetes` → path to your kubeconfig |
| Are behind a corporate proxy | `http_proxy` / `https_proxy` / `no_proxy` |
| Already run cert-manager, Istio, Prometheus… | `<component>_enabled` → `false` |

### The settings most people touch

| Setting | Default | What it does |
|---|---|---|
| `base_domain_name` | `solutions.ai` | Every service URL derives from this — `grafana.<domain>`, `keycloak.<domain>`, `inference.<domain>` |
| `auth_provider` | `keycloak` | Which auth stack deploys and how API calls are authorized — see [Auth provider](#auth-provider) |
| `storage_backend` | `local-path` | Where persistent data lives — see [Storage](#storage) |
| `gateway_tls_mode` | `selfsigned` | Auto-generated CA, or bring your own — see [TLS](#tls) |
| `kubernetes_accelerator` | `cpu` | Compute target for inference (Intel® Xeon®) |
| `observability_enabled` | `true` | Deploy Prometheus + Grafana + Loki + Tempo |
| `velero_enabled` | `false` | Deploy the backup mechanism (Velero + CSI snapshots). Needs `storage_backend: nfs` or `ceph`; each solution documents its own backup and restore procedure |

Everything else is safe to leave alone until you have a reason to change it.

---

## Changing values

**Either** edit `env/<name>/global_config.yaml` and re-run the install:

```yaml
auth_provider: "litellm"
```

**Or** override at install time without editing anything:

```bash
./es_auto_installer.sh install inference --env local -- -e auth_provider=litellm
```

Anything after the bare `--` is passed straight to Ansible, so any variable in
this reference can be overridden that way.

**Precedence** (highest wins):

```
CLI -e override  >  global_config.yaml  >  config.<solution>.yaml  >  role defaults
```

---

## Full template

The complete `global_config.yaml` as `init` generates it. Every value shown is
the default, so this doubles as a reference for what you can set:

```yaml
# =============================================================================
# global_config.yaml — Main configuration for your deployment
# =============================================================================

# --- Networking & Domain ---
base_domain_name: "solutions.ai"          # Base domain for all service URLs
                                           # (e.g. grafana.solutions.ai, litellm.solutions.ai)

# --- TLS ---
gateway_tls_mode: "selfsigned"            # "selfsigned" = auto-generated CA
                                           # "custom"     = provide your own cert/key below
# gateway_tls_cert_file: "/path/to/cert.pem"   # Only needed when gateway_tls_mode: custom
# gateway_tls_key_file:  "/path/to/key.pem"    # Must cover *.<base_domain_name>

# --- Authentication ---
auth_provider: "keycloak"                 # "keycloak" = full OIDC with SSO, RBAC, identity mgmt
                                           # "litellm"  = lightweight virtual-key auth (no Keycloak)

# --- Storage ---
storage_backend: "local-path"             # "local-path" = node-local (single-node only)
                                           # "nfs"        = shared NFS (required for multi-node)
                                           # "ceph"       = replicated block via Rook-Ceph
                                           # "custom"     = use your cluster's own StorageClass
# storage_custom_storageclass: ""          # required when storage_backend=custom

# --- Compute ---
kubernetes_accelerator: "cpu"             # "cpu" = Intel® Xeon® (default)
kubernetes_cpu_policy: "nri-balloons"     # "nri-balloons"   = NUMA-aware pinning (recommended)
                                           # "kubelet-static" = kubelet static CPU manager
                                           # "best-effort"    = no pinning

# --- Observability ---
observability_enabled: true               # Deploy Prometheus, Grafana, Loki, Tempo

# --- Backup ---
velero_enabled: false                     # Deploy Velero + CSI snapshots (needs nfs or ceph)

# --- Networking ---
gateway_request_timeout: "600s"           # Envoy timeout for long LLM responses
kubernetes_cluster_name: "cluster.local"  # Kubernetes DNS suffix
kubernetes_kube_proxy_mode: "nftables"    # "nftables" | "iptables" | "ipvs"
node_topology_enabled: true               # Soft affinity for platform vs inference pods

# --- Object Store (for Loki, Tempo, Langfuse) ---
object_store_backend: "minio"             # "minio" | "rustfs" | "seaweedfs" | "external"
minio_storage_size: "10Gi"

# --- BYO Cluster (skip Kubernetes provisioning) ---
# existing_kubernetes: "/path/to/kubeconfig"

# --- Proxy (uncomment if behind corporate proxy) ---
# http_proxy:  "http://proxy.example.com:8080"
# https_proxy: "http://proxy.example.com:8080"
# no_proxy: "localhost,127.0.0.1,10.233.0.0/18,10.233.64.0/18,.svc,.cluster.local,<node-subnet>"

# --- Component toggles (set false to skip) ---
# cert_manager_enabled: true
# metallb_enabled: true
# istio_enabled: true
# postgresql_enabled: true
```

### Field summary

| Field | What it controls |
|---|---|
| `base_domain_name` | All service URLs derive from this (e.g. `grafana.<domain>`, `litellm.<domain>`) |
| `gateway_tls_mode` | How TLS certs are provisioned — auto-generated CA or bring your own |
| `auth_provider` | Which auth stack deploys and how API requests are authorized |
| `storage_backend` | Where persistent data lives — must be shared storage for multi-node |
| `kubernetes_accelerator` | Compute target for inference workloads (Intel® Xeon® CPU) |
| `kubernetes_cpu_policy` | CPU pinning strategy for inference workloads |
| `observability_enabled` | Whether the full monitoring stack (Prometheus/Grafana/Loki/Tempo) is deployed |
| `velero_enabled` | Whether the backup mechanism is deployed — needs snapshot-capable storage |
| `gateway_request_timeout` | Max time the gateway waits for a model response before timing out |
| `object_store_backend` | Backend for log/trace/blob storage |
| `existing_kubernetes` | Point to an existing kubeconfig to skip cluster provisioning entirely |

---

# Detailed reference

## TLS

The gateway terminates all external HTTPS. There is no plaintext mode.

| `gateway_tls_mode` | What happens |
|---|---|
| `selfsigned` (default) | cert-manager creates an internal root CA that signs a wildcard certificate for `*.<base_domain_name>`. The CA is exported to `env/<name>/logs/ai-solutions-ca.crt` after install — import it once to remove browser warnings. |
| `custom` | You supply the certificate and key. |

**Custom TLS:**

```yaml
gateway_tls_mode: "custom"
gateway_tls_cert_file: "/path/to/cert.pem"   # must cover <base_domain> and *.<base_domain>
gateway_tls_key_file:  "/path/to/key.pem"
```

**Import the self-signed CA:**

| Target | How |
|---|---|
| Firefox | Settings → Privacy & Security → Certificates → View Certificates → Authorities → Import |
| Chrome / Edge | Settings → Privacy → Security → Manage certificates → Authorities → Import |
| Linux system-wide | `sudo cp env/<name>/logs/ai-solutions-ca.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates` |
| macOS | `sudo security add-trusted-cert -d -k /Library/Keychains/System.keychain env/<name>/logs/ai-solutions-ca.crt` |

---

## Auth provider

Set via `auth_provider`. Controls which authentication stack is deployed and how model requests are authorized.

| `auth_provider` | What's deployed | Request path | Best for |
|---|---|---|---|
| `keycloak` (default) | Keycloak + keycloak_config | Envoy Gateway validates JWT → KServe predictor | Production SSO, RBAC, per-user tokens |
| `litellm` | LiteLLM + Langfuse + Valkey (Keycloak **not** deployed) | Envoy Gateway extAuth → LiteLLM `/key/verify` → KServe | Multi-tenant, per-key budgets, simpler setup |

Switching `auth_provider` changes which components deploy and how the gateway `SecurityPolicy` is configured. It is best set before the first install.

---

## Storage

| `storage_backend` | Description | When to use |
|---|---|---|
| `local-path` (default) | Node-local storage (ReadWriteOnce) | Single-node only |
| `nfs` | NFS server auto-provisioned on the first control-plane node at `/data/nfs` (ReadWriteMany), served through the `csi-driver-nfs` CSI driver so volumes can be snapshotted. The StorageClass is named `nfs-client` — kept from the provisioner it replaced, since layers above this one hardcode that name | **Required for multi-node** — model weights must be accessible from every node. Also required for `velero_enabled` (backup needs a CSI-capable class) |
| `ceph` | Replicated block storage via Rook-Ceph (ReadWriteMany + redundancy) | Multi-node with raw block devices, production durability |
| `custom` | Nothing is installed — an existing StorageClass named by you is used as-is | Your cluster already runs its own CSI driver |

> **Multi-node warning:** the installer aborts if `storage_backend: local-path` is detected on a multi-node cluster. Change it to `nfs` before running.

**Custom (bring your own CSI):**

```yaml
storage_backend: custom
storage_custom_storageclass: "my-csi-sc"   # required
storage_custom_rwx: true                   # false = RWO-only class, single-node only
```

Every component — core roles and the bundled third-party charts alike — provisions without naming a StorageClass, so the class you name must be the cluster's **only** default; that annotation is what makes the rest of the stack inherit it:

```console
kubectl annotate storageclass my-csi-sc storageclass.kubernetes.io/is-default-class=true --overwrite
```

The installer verifies the class exists and is the sole default, then stops there. It never creates, patches, or deletes it — the driver is yours and is likely shared with workloads outside the platform, so `teardown` leaves it alone too.

`storage_custom_rwx` is the one thing the installer cannot work out for itself: ReadWriteMany capability is a property of the CSI driver, not a field on the StorageClass. It decides the access mode of the shared `model-store` PVC, which the weight-download job and every predictor pod mount at once. Leave it `true` for a shared-filesystem class (NFS, CephFS, ontap-nas, EFS, Azure Files); set it `false` only for an RWO-only class such as EBS, `ceph-block`, or a hostPath provisioner — which restricts you to a single node, and the installer aborts if it finds more. Getting it wrong leaves `model-store` `Pending`, and since `accessModes` is immutable, recovering means deleting the PVC by hand.

**Ceph additional settings:**

```yaml
storage_backend: ceph
rook_ceph_devices:
  node-1: [/dev/nvme1n1]
  node-2: [/dev/nvme1n1]
# rook_ceph_teardown_wipe_devices: false   # set true to auto-wipe on teardown
```

---

## Proxy

Uncomment and set these if the installer host or target nodes are behind a corporate proxy.

```yaml
http_proxy:  "http://proxy.example.com:8080"
https_proxy: "http://proxy.example.com:8080"
# no_proxy must include Kubernetes CIDRs and node subnets.
# Get node subnets with: ip route | grep -v default | awk '{print $1}' | grep '/'
no_proxy: "localhost,127.0.0.1,10.233.0.0/18,10.233.64.0/18,.svc,.cluster.local,.monitoring,<node-subnet>"
```

> **`kubectl` and proxy:** if `HTTP(S)_PROXY` is set in your shell, `kubectl` may fail with `Forbidden` errors because API calls get routed through the proxy. Add cluster and pod CIDRs to `no_proxy` in your shell environment:
> ```bash
> export no_proxy="localhost,127.0.0.1,10.233.0.0/18,10.233.64.0/18,.svc,.cluster.local"
> export NO_PROXY="$no_proxy"
> ```

---

## CPU policy

`kubernetes_cpu_policy` is the single switch for CPU pinning. Everything downstream — NRI plugin installation, kubelet configuration, and model manifest rendering — is derived from it.

| Value | What it does | When to use |
|---|---|---|
| `nri-balloons` (default) | Installs the NRI balloons plugin. NUMA-aware pinning with hyperthread isolation. Pods use normal (Burstable) QoS. | Recommended — best performance on multi-NUMA Xeon nodes |
| `kubelet-static` | Configures kubelet's static CPU manager (`full-pcpus-only`). Pods become Guaranteed QoS. No NRI plugin. | Clusters that cannot run NRI |
| `best-effort` | No pinning — default scheduler placement | Development or small clusters where pinning isn't needed |

See [NRI CPU Balloons](nri_cpu_balloons.md) for per-node balloon configuration and sizing.

---

## Disable components

Set `<component>_enabled: false` to skip anything you already manage:

```yaml
cert_manager_enabled: false     # already have cert-manager
metallb_enabled: false          # using a cloud load balancer or external LB
istio_enabled: false            # bring your own service mesh
observability_enabled: false    # skip the Prometheus/Grafana/Loki stack
postgresql_enabled: false       # using an external PostgreSQL
```

---

## Networking

| Setting | Default | Description |
|---|---|---|
| `gateway_request_timeout` | `600s` | Envoy request timeout — raised from the 15s default for long LLM responses |
| `kubernetes_cluster_name` | `cluster.local` | Kubernetes cluster DNS suffix |
| `kubernetes_kube_proxy_mode` | `nftables` | `nftables` \| `iptables` \| `ipvs` |
| `node_topology_enabled` | `true` | Soft affinity: platform pods prefer control-plane nodes, inference pods prefer workers. See [Node Topology](node_topology.md). |

---

## Object store

Backend for Loki log storage, Tempo traces, and Langfuse blobs.

```yaml
object_store_backend: "minio"    # minio (default) | rustfs | seaweedfs | external
minio_storage_size: "10Gi"
```

---

## Admin passwords

Keycloak and Grafana admin passwords are **auto-generated** (24 random
characters) on first install and stored in Kubernetes secrets. Re-running the
install preserves the existing password rather than rotating it.

Retrieve them after install — the username is `admin` in both cases:

```bash
# Grafana
kubectl get secret -n monitoring grafana-admin-credentials \
  -o jsonpath='{.data.password}' | base64 -d; echo

# Keycloak
kubectl get secret -n keycloak keycloak-admin-secret \
  -o jsonpath='{.data.password}' | base64 -d; echo
```

To choose the passwords yourself, set them before the first install — either in
`global_config.yaml`:

```yaml
keycloak_admin_password: "your-password"
observability_grafana_admin_password: "your-password"
```

or as install-time overrides:

```bash
./es_auto_installer.sh install inference -- \
  -e keycloak_admin_password=your-password \
  -e observability_grafana_admin_password=your-password
```

---

## BYO cluster

```yaml
existing_kubernetes: "/absolute/path/to/kubeconfig"   # skips all Kubespray phases
```

---

## Version pinning

Component versions are set in `global_config.yaml` and can be overridden at runtime:

```bash
./es_auto_installer.sh install envoy_gateway --env local \
  -- -e envoy_gateway_version=1.4.0
```

Or edit `global_config.yaml` and re-run `install <component>`.

---

## Per-Layer Solution Configs

In addition to `global_config.yaml`, each solution layer has its own configuration file seeded by `init <layer>`:

- **`env/<name>/config.inference.yaml`** — seeded by `init inference` from the inference repo's `config.yaml`
- **`env/<name>/config.erag.yaml`** — seeded by `init erag` from the chosen flavour's `config.yaml` (default: `chatqna`)

These files are **flat** override surfaces loaded via `-e @file`, so nested dicts clobber role defaults rather than merging. The installer loads them in manifest order (dependencies first), then loads `global_config.yaml`, so `global_config.yaml` wins over layer configs. This lets you set solution-specific defaults in your layer config and override them globally when needed.

Example: `config.erag.yaml` sets RAG pipeline parameters like `ui_enabled`, `edp_enabled`, `vector_databases_enabled`. To override globally, set those same variables in `global_config.yaml`.

---

## Related Docs

| If you want to… | Go to |
|---|---|
| See every CLI command and flag that reads this file | [CLI Reference](cli.md) |
| Configure per-node CPU pinning (`kubernetes_cpu_policy: nri-balloons`) in depth | [NRI CPU Balloons](nri_cpu_balloons.md) |
| Set up soft affinity for platform vs. inference workloads (`node_topology_enabled`) | [Node Topology & Workload Placement](node_topology.md) |
| Go from a fresh clone to a running cluster using these settings | [Deployment Guide](../deploy/install_platform.md) |
