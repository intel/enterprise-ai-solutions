# NetApp ONTAP and Trident

## Quickstart

Two flows. Pick the one that matches what you want on the cluster:

- **Flow A: array-backed persistent volumes.** Trident CSI serves every PVC in the
  platform and serving stack (the model store, PostgreSQL, observability) from the array.
  This is what you want if you are deploying model serving, or platform services only.
- **Flow B: Flow A plus ONTAP S3 as the document store for Intel AI for Enterprise RAG.**
  Everything in Flow A, and RAG documents live in an ONTAP bucket instead of the
  in-cluster object store. Only choose this if the SVM already runs an object-store
  server.

Both flows assume the array side already exists. The installer creates nothing on it: see
[Prerequisites on the ONTAP side](#prerequisites-on-the-ontap-side) before you start.

### Flow A: array-backed persistent volumes

1. Prepare the installer host. Once per machine, installs Python, `yq`, `kubectl`, `helm`
   and the installer venv:

   ```bash
   ./es_auto_installer.sh configure
   ```

2. Create the environment. `init` takes the **layer** you intend to deploy; `--env` names
   the environment directory under `env/` and defaults to `local`:

   ```bash
   ./es_auto_installer.sh init inference --env prod
   ```

   Choose the layer by what you want running. `inference` gives you the model-serving
   stack and pulls `platform` and `infrastructure` with it, which is the usual choice.
   `init platform` is valid too, for platform services with no model serving; it needs no
   external repository, so it clones nothing and seeds no layer config. Either way this
   creates `env/prod/` with `global_config.yaml`, `nodes.yaml` and
   `inventory/hosts.yaml`; `init inference` additionally seeds `env/prod/config.inference.yaml`
   and `env/prod/models.yaml`.

3. Point the environment at the array. In `env/prod/global_config.yaml`:

   ```yaml
   storage_backend: netapp-trident
   ontap_management_lif: "10.0.0.100"   # cluster or SVM management LIF, TCP 443
   ontap_data_lif: "10.0.0.101"         # NFS data LIF, TCP 2049 + 111
   ontap_svm: "svm_ai"                  # SVM that owns both LIFs
   ontap_username: "vsadmin"            # ONTAP user with http/ontapi application access
   ontap_password: "<password>"         # written only into a Kubernetes Secret
   ```

4. Deploy the layer. Dependency layers come along automatically, so this one command
   provisions Kubernetes, the storage backend, the platform and the serving stack:

   ```bash
   ./es_auto_installer.sh install inference --env prod
   ```

   Storage preflight runs before the Trident chart is installed: it probes both LIFs from
   every cluster node, installs the NFS client packages and resolves LIF hostnames per
   node. A failure there names the node, the port and the remedy.

### Flow B: Flow A plus ONTAP S3 as the RAG document store

1. Same host preparation:

   ```bash
   ./es_auto_installer.sh configure
   ```

2. Initialize the `erag` layer. This clones the inference and RAG repositories at their
   pinned revisions and seeds `env/prod/config.inference.yaml`, `env/prod/models.yaml`
   and `env/prod/config.erag.yaml` from the default `chatqna` pipeline preset:

   ```bash
   ./es_auto_installer.sh init erag --env prod
   ```

3. In `env/prod/global_config.yaml`, the five Flow A values and `storage_backend`, plus
   the object-store LIF:

   ```yaml
   storage_backend: netapp-trident
   ontap_management_lif: "10.0.0.100"
   ontap_data_lif: "10.0.0.101"
   ontap_svm: "svm_ai"
   ontap_username: "vsadmin"
   ontap_password: "<password>"
   ontap_s3_data_lif: "10.0.0.102"      # object-store server data LIF, NOT the NFS one
   # ontap_s3_port: <port>              # only if the object-store server is not on 443
   ```

   `ontap_s3_data_lif` is a **different LIF** from `ontap_data_lif`. Setting it is what
   activates the dedicated gateway S3 listener and the reverse proxy. Set `ontap_s3_port`
   too whenever the object-store server does not answer on 443, so preflight probes the
   right port.

4. In `env/prod/config.erag.yaml`, the object-store user credentials:

   ```yaml
   edp_s3_compatible_access_key_id: "<access key>"
   edp_s3_compatible_secret_access_key: "<secret key>"
   ```

5. Deploy the RAG layer. It depends on `platform` and `inference`, so this pulls
   `infrastructure`, `platform` and `inference` first, in that order:

   ```bash
   ./es_auto_installer.sh install erag --env prod
   ```

## What the integration provides

Two capabilities. They share the ONTAP configuration values but are independent, and
either can be used without the other:

1. **Trident CSI for persistent volumes.** `storage_backend: netapp-trident` installs the
   NetApp Trident operator, registers an ONTAP backend and creates a cluster-default
   StorageClass. Every PVC in the stack (the inference model store, PostgreSQL, the
   vector database, observability) is then served from the array. This is the
   `roles/netapp_trident_csi` role, selected through the backend registry in
   `roles/storage/defaults/main.yaml`.

2. **ONTAP S3 as the document store for the RAG layer (optional).** When the SVM also
   runs an object-store server, the RAG data-prep service (EDP) can use it instead of
   the in-cluster SeaweedFS. Because the object-store data LIF sits on the storage
   network and browsers upload straight to the presigned URL they are handed, the
   endpoint is published through the platform Envoy Gateway as a reverse proxy. The
   proxy objects are owned by the RAG layer (`ext/enterprise.ai-erag`, role `app_edp`);
   the dedicated gateway listener that makes SigV4 signatures survive the hop is owned
   by `roles/envoy_gateway`.

Capability 1 with the in-cluster object store is a complete, supported deployment
([Flow A](#flow-a-array-backed-persistent-volumes)). Add capability 2 only if you want
documents to live on the array
([Flow B](#flow-b-flow-a-plus-ontap-s3-as-the-rag-document-store)).

## Prerequisites on the ONTAP side

**The installer creates nothing on the array.** It has no ONTAP REST API integration at
all: the only array contact is a set of TCP port probes. No SVM, LIF,
export policy, aggregate, object-store server, bucket or user is ever created, changed
or deleted by this installer. Everything below must exist before you run it.

| Prerequisite | Why |
|---|---|
| ONTAP 9.16.1P4 or newer (recommended) | The pairing the pinned Trident 25.10 chart is qualified against, and the floor for the ONTAP S3 presigned-URL behaviour the RAG layer relies on. ONTAP 9.11 is the floor for the REST-based backend the role configures (`ontap_use_rest: true`). |
| A licensed ONTAP system, with the NFS protocol and FlexClone entitled | Trident is Apache-2.0 and needs no license of its own, but every capability it drives is an ONTAP feature. |
| An SVM with NFS enabled | Trident's `ontap-nas` driver provisions FlexVols and exports them over NFS. |
| A management LIF reachable on TCP 443 from **every** cluster node | The Trident controller can be scheduled on any node. Preflight probes this from each node and fails with the full node-by-port matrix. |
| An NFS data LIF reachable on TCP 2049 and 111 from every **schedulable** node | 2049 is nfsd; some export configurations still need rpcbind on 111 for mount negotiation, so both are probed. Only nodes that can run a pod mount a volume, so preflight probes these ports from the `kube_node` group - a `NoSchedule`-tainted control plane off the storage VLAN is not a failure. An inventory with no `kube_node` group is probed from every node. |
| An NFS export policy on that SVM permitting the Kubernetes node network | Missing or read-only rules produce a PVC that binds and a pod that cannot write. The end-to-end validation test catches this with a `test -w` inside the volume, which no reachability probe can replace. |
| An ONTAP user with `http`/`ontapi` application access to that SVM | Used by the Trident controller. Credentials are placed in a Kubernetes Secret; nothing else reads them. |
| For ONTAP S3 only: an object-store server, a bucket and an S3 user with access keys | The RAG layer needs the keys; it cannot create them. On the array: `vserver object-store-server create`, `... bucket create`, `... user create`. |
| Clocks in sync on the array and on the nodes (NTP) | S3 SigV4 rejects a signed request outside a 15-minute window, and the presigned DELETE the RAG UI issues carries a 60-second expiry, so a clock offset breaks file deletion first. |

Node-side prerequisites are handled for you on an installer-provisioned cluster: the
NFS client packages (`nfs-common` on Debian family, `nfs-utils` on RHEL family) are
installed on every inventory node during preflight, and `/sbin/mount.nfs` is asserted
afterwards. On a BYO cluster (`existing_kubernetes`) the installer has no inventory to
work with, so those packages must already be present on every node that mounts a
volume.

## What you have to configure

The array topology goes in `env/<name>/global_config.yaml`, created by
`./es_auto_installer.sh init <layer> --env <name>`. The "NetApp ONTAP" block in
`configs/defaults/global_config.yaml` is the same list, commented out, with inline notes.

Settings that belong to a solution layer rather than to the array live in that layer's
own config. For the RAG layer that is `env/<name>/config.erag.yaml`, seeded from the
pipeline flavour you chose at `init` time. The installer's `global_config.yaml` carries no
layer-specific variables.

### Trident CSI: five values

| Variable | Meaning |
|---|---|
| `ontap_management_lif` | Cluster or SVM management LIF (probed on `ontap_mgmt_port`, default 443) |
| `ontap_data_lif` | NFS data LIF that Trident mounts volumes over. A `data`-role LIF whose service policy includes `data-nfs`, never the management LIF: a management LIF answers on 443 and refuses 2049/111. |
| `ontap_svm` | SVM that owns both LIFs |
| `ontap_username` | ONTAP user with `http`/`ontapi` application access |
| `ontap_password` | Kept out of every log; written only into a Kubernetes Secret |

`storage_backend: netapp-trident` is what selects the array; the `ontap_*` values on
their own do nothing. With the backend set, preflight asserts that all five are filled in
before Kubespray runs, so a missing value costs seconds rather than surfacing 40 minutes
later as a `TridentBackendConfig` that never reaches `Bound`.

Both LIFs accept an IPv4 address or a hostname. A hostname must resolve **on the cluster
nodes**, not on the installer host: Trident resolves LIFs from the node it runs on, so
preflight runs `getent hosts` from each node and reports every unresolvable pair.

### ONTAP S3 as the RAG document store: three more

| Variable | Where it goes | Meaning |
|---|---|---|
| `ontap_s3_data_lif` | `global_config.yaml` | Data LIF of the SVM's object-store server. This is a **different LIF** from `ontap_data_lif`, which is the NFS one. Leaving it empty means the whole S3 path is inactive. |
| `edp_s3_compatible_access_key_id` | `config.erag.yaml` | Access key of the ONTAP object-store user |
| `edp_s3_compatible_secret_access_key` | `config.erag.yaml` | Its secret key |

The array topology is the installer's concern; the credentials belong to the layer that
consumes them. See `../../ext/enterprise.ai-erag/docs/customize/object_store.md` for the RAG side.

`ontap_s3_data_lif` is what activates the gateway's dedicated S3 listener and, together
with the credentials, the reverse proxy in the RAG layer. `edp_rbac_enabled` must stay
`false` (its default): RBAC mode makes EDP sign requests as the logged-in user through
STS `AssumeRoleWithWebIdentity`, which ONTAP S3 does not implement. The RAG layer asserts
that too, with the reason.

Set `ontap_s3_port` as well if the object-store server does not answer on 443. Leaving it
unset is not the same as setting 443: the RAG layer defaults to 443, but the platform
preflight only adds the S3 endpoint to the reachability matrix when the port is set
explicitly. Setting it is the difference between a blocked S3 port failing in seconds at
preflight and failing on the first upload from the RAG UI.

### Optional knobs worth knowing

| Variable | Default | Effect |
|---|---|---|
| `ontap_driver` | `ontap-nas` | The NAS drivers (`ontap-nas`, `ontap-nas-economy`, `ontap-nas-flexgroup`) serve ReadWriteOnce, ReadOnlyMany and ReadWriteMany over NFS. The SAN drivers are rejected: they are ReadWriteOnce only and need iSCSI/multipath node preparation this role does not perform. |
| `ontap_preflight_online` | `true` | `false` skips every check that needs the array, so the role can be rehearsed without one. |
| `ontap_teardown_delete_pvcs` | `false` | Explicit consent for a component-level teardown of `storage` to delete application PVCs. See [teardown](#validating-and-tearing-down). |
| `ontap_s3_tls` | `true` | `false` proxies plaintext HTTP to the object-store port (TLS is still terminated at the gateway). |
| `ontap_s3_ca_cert_file` | `""` | PEM on the installer host that signed the ONTAP S3 certificate, for the verified upstream-TLS mode. |

Everything else is a role default in `roles/netapp_trident_csi/defaults/main.yaml`
(namespace, chart version, StorageClass name, timeouts, validation and teardown
behaviour, the supported-driver list). Those are documented in place and rarely need
touching; they are deliberately not repeated in the environment template.

### What the installer derives

Five values, or eight with ONTAP S3 as the document store, are the whole operator input:
the array topology and two secrets. Everything else about the storage path is derived.
The list matters for two reasons: it tells you what **not** to set, and when a value looks
wrong in a running deployment it tells you where it came from.

| Derived value | How |
|---|---|
| The CSI backend selection | Resolved from `storage_backend` |
| Trident operator version, namespace, StorageClass name, backend name | Pinned role defaults (chart `100.2510.0`, namespace `trident`, class `netapp-trident`, backend `ontap-nas`) |
| The ONTAP aggregate | Never specified; ONTAP picks it |
| Access mode for the shared model cache | `ReadWriteMany` on every backend except `local-path` |
| StorageClass for the model store | Empty, meaning "use the cluster default", which this backend asserts is exactly one, and exactly its own, at install time |
| Activation of the ONTAP S3 reverse proxy | Implied by `ontap_s3_data_lif` |
| `edp_storage_type: s3compatible` | From `ontap_s3_data_lif` plus an access key on a `netapp-trident` cluster. Set once in `app_pre_install`, so the data-prep layer, the UI and the pod checks all agree on it. Setting it yourself still wins, and the RAG layer then reports the contradiction rather than picking a side. |
| EDP internal URL, external URL, region, bucket filter, two certificate-verify flags | From the LIF, the port, the TLS mode, `base_domain_name` and `routing_mode` |
| `presignedUrlCredentialsSystemFallback`, the scheduled bucket sync and its period | Role defaults; the scheduled sync is switched on automatically because ONTAP S3 has no bucket notifications |
| The MetalLB address pool and L2 advertisement | The `metallb` component, with the range auto-detected |
| The Kubespray inventory | Generated from `env/<name>/nodes.yaml` |
| The `/etc/hosts` line for the client workstation | Printed by validation, with the real gateway load-balancer address |

## Validating and tearing down

Installation is the [Quickstart](#quickstart): you deploy a layer and the storage
component comes with it. This section covers what happens afterwards.

Validate at the layer you deployed:

```bash
./es_auto_installer.sh validate inference --env prod   # or: validate platform / validate erag
```

A layer validation includes the storage checks, because `storage` is part of the
`infrastructure` layer that every layer depends on. To re-run just the storage checks
while working through a failure, target the component:

```bash
./es_auto_installer.sh validate storage --env prod
```

Teardown is also a layer operation, and it resolves **upward**: the target plus every
layer that depends on it. So `teardown infrastructure` removes the RAG layer, the serving
stack, the platform, the storage backend and then the cluster itself, in that order, and
it asks for confirmation first because Kubespray reset destroys the cluster.
Tearing down a single layer, for example `teardown erag`, leaves everything below it
running.

```bash
./es_auto_installer.sh teardown erag --env prod             # just the RAG layer
./es_auto_installer.sh teardown infrastructure --env prod   # everything, cluster included
```
