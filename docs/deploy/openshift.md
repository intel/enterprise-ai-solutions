# Deploy on OpenShift

[← Docs Index](../README.md)

How to prepare a customer-owned OpenShift cluster with Red Hat OpenShift AI, install every prerequisite from the OperatorHub (Software Catalog) marketplace, and then run the installer on top of it.

On OpenShift the installer does not provision cluster-wide services. You install the operators from the marketplace and create their cluster-level instances. The installer then checks that everything is in place and deploys only application-scoped resources into its own namespaces.

> [!WARNING]
> **Preview: OpenShift support is not yet complete.** This guide describes the target flow. Several installer behaviours it relies on are **planned, not yet implemented**. They are marked _(planned)_ below and summarised in [Known gaps](#known-gaps). Today the installer only [detects the platform](#platform-detection); the components start using the result in follow-up changes.
>
> Until the OpenShift-safe teardown lands, **never run `./es_auto_installer.sh teardown infrastructure` or `teardown platform` against an OpenShift cluster.** The current teardown roles delete CRDs by API group (for example all `gateway.networking.k8s.io`, `monitoring.coreos.com`, and `*kserve*` CRDs). On OpenShift those CRDs are owned by the cluster and by OpenShift AI.

---

## Who owns what

| Owner | Installs | Examples |
|---|---|---|
| **Cluster admin (you), from the marketplace** | Operators, plus the cluster-scoped singleton each one needs | LVMS + `LVMCluster`, cert-manager, Service Mesh 3 + `Istio`/`IstioCNI`/`ZTunnel`, CloudNativePG, Red Hat build of Keycloak, NFD + `NodeFeatureDiscovery`, LWS, Custom Metrics Autoscaler + `KedaController`, OpenShift AI + `DataScienceCluster`; optionally MetalLB |
| **Installer (`es_auto_installer.sh`)** | Resources scoped to the application, in namespaces it creates and labels | Enabling user-workload monitoring _(planned)_, CNPG `Cluster`, `Keycloak` instance and realm, `ServiceMonitor`/`PodMonitor`, LiteLLM, Langfuse, inference models, eRAG applications |

The rule: **the installer never installs, upgrades, or deletes an operator, a CRD, or a cluster singleton that it did not create.** The one exception is user-workload monitoring: the installer switches it on by merging `enableUserWorkload: true` into `cluster-monitoring-config`, keeps any settings already there, and never switches it off. Anything the installer creates carries an ownership label, and teardown removes only labelled objects _(planned)_.

---

## Validated environment

| Item | Version tested |
|---|---|
| OpenShift Container Platform | 4.22 (single node, RHCOS, CRI-O) |
| Red Hat OpenShift AI | 3.3.4 (`stable-3.3` channel) |
| Storage | LVM Storage 4.22 (`lvms-vg1`, RWO) |

Multi-node clusters are expected to work if they have a ReadWriteMany StorageClass (ODF CephFS or Trident). They have not been validated yet.

---

## Prerequisites at a glance

Install these in the order listed. "Required" means the installer's preflight check fails without it _(planned)_.

| # | Component | Package | Catalog | Channel (tested) | Namespace | Required |
|---|---|---|---|---|---|---|
| 1 | Default StorageClass | `lvms-operator` (or `odf-operator`, `trident-operator`) | redhat-operators | `stable-4.22` | `openshift-storage` | **Yes** |
| 2 | cert-manager | `openshift-cert-manager-operator` | redhat-operators | `stable-v1` | `cert-manager-operator` | **Yes** |
| 3 | Service Mesh 3 (ambient) | `servicemeshoperator3` | redhat-operators | `stable` | `openshift-operators` | **Yes** |
| 4 | Gateway API | built in (`openshift-ingress`) | — | — | — | **Yes** (nothing to install) |
| 5 | CloudNativePG | `cloudnative-pg` | certified-operators | `stable-v1` | `openshift-operators` | **Yes** |
| 6 | Red Hat build of Keycloak | `rhbk-operator` | redhat-operators | `stable-v26.6` | `keycloak` | When `auth_provider: keycloak` |
| 7 | Custom Metrics Autoscaler | `openshift-custom-metrics-autoscaler-operator` | redhat-operators | `stable` | `openshift-keda` | **Yes** (autoscaling) |
| 8 | Node Feature Discovery | `nfd` | redhat-operators | `stable` | `openshift-nfd` | **Yes** |
| 9 | LeaderWorkerSet | `leader-worker-set` | redhat-operators | `stable-v1.0` | `openshift-lws-operator` | **Yes** (llm-d serving) |
| 10 | Red Hat Connectivity Link + `Kuadrant` | `rhcl-operator` | redhat-operators | `stable` | `kuadrant-system` | **Yes** (OpenShift AI `LLMInferenceService`) |
| 11 | OpenShift AI + `DataScienceCluster` | `rhods-operator` | redhat-operators | `stable-3.3` | `redhat-ods-operator` | **Yes** |
| 12 | MetalLB | `metallb-operator` | redhat-operators | `stable` | `metallb-system` | Optional (multi-node, dedicated VIP) |
| 13 | Logging, Loki, Tempo, OpenTelemetry | `cluster-logging`, `loki-operator`, `tempo-product`, `opentelemetry-product` | redhat-operators | `stable-6.6` (logging/Loki) | `openshift-logging`, `openshift-operators-redhat`, `openshift-tempo-operator`, `openshift-opentelemetry-operator` | Optional |
| 14 | Cluster Observability Operator | `cluster-observability-operator` | redhat-operators | `stable` | `openshift-cluster-observability-operator` | Optional (dashboards and UI plugins) |

Channels move between OpenShift releases. Before you install, confirm each package's default channel and suggested namespace:

```bash
oc get packagemanifest <package> -n openshift-marketplace \
  -o jsonpath='{.status.defaultChannel}{"  "}{.status.channels[0].currentCSVDesc.annotations.operatorframework\.io/suggested-namespace}{"\n"}'
```

**No marketplace equivalent** exists for the following. The installer still deploys them with Helm into its own namespaces: LiteLLM, Langfuse, Envoy Gateway / Envoy AI Gateway (see [Known gaps](#known-gaps)), and the eRAG applications. The NFS CSI driver is **not used** on OpenShift. NRI CPU balloons work as on Kubernetes: CRI-O enables NRI, and the installer sets the chart's `openshift.grant-scc` so its plugin gets an SCC.

---

## Installing an operator: the pattern

Every operator in the table installs the same way: a Namespace, an OperatorGroup, and a Subscription. You can use the web console instead (**Ecosystem → Software Catalog → Install**). The result is the same.

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: <namespace>
---
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata:
  name: <namespace>
  namespace: <namespace>
spec:
  targetNamespaces:            # omit `spec` entirely for AllNamespaces-only operators
    - <namespace>
---
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: <package>
  namespace: <namespace>
spec:
  name: <package>
  channel: <channel>
  source: redhat-operators     # or certified-operators
  sourceNamespace: openshift-marketplace
  installPlanApproval: Manual  # recommended: upgrades happen only when you approve them
```

Skip the Namespace and the OperatorGroup for operators that install into `openshift-operators`; that namespace already has a global OperatorGroup.

Wait for the operator to report `Succeeded`:

```bash
oc get csv -n <namespace> -w
```

With `installPlanApproval: Manual`, approve the first InstallPlan:

```bash
oc get installplan -n <namespace>
oc patch installplan <name> -n <namespace> --type merge -p '{"spec":{"approved":true}}'
```

---

## Step-by-step

### 1. Storage: default StorageClass

The installer needs a **default** StorageClass. It never creates, modifies, or demotes StorageClasses on OpenShift.

| Option | When to use | Access modes |
|---|---|---|
| **LVM Storage (LVMS)** (recommended for single node) | Single node with spare local disks | RWO only |
| OpenShift Data Foundation | Multi-node, three or more nodes with local disks | RWO (RBD), RWX (CephFS) |
| NetApp Trident | An existing ONTAP array | RWO, RWX |

**LVMS (tested).** Install `lvms-operator` with the [pattern](#installing-an-operator-the-pattern), using namespace `openshift-storage` and channel `stable-4.22`. Then create the volume group.

The disks you list must be **completely empty**. LVMS refuses a disk that has any partition table, including an empty GPT. Find the disks:

```bash
oc debug node/<node> -- chroot /host lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,PTTYPE
oc debug node/<node> -- chroot /host ls -l /dev/disk/by-path/
```

If a disk you want to use still has a partition table, wipe it. **This destroys all data on the disk.**

```bash
oc debug node/<node> -- chroot /host wipefs -a /dev/<disk>
```

```yaml
apiVersion: lvm.topolvm.io/v1alpha1
kind: LVMCluster
metadata:
  name: lvmcluster
  namespace: openshift-storage
spec:
  storage:
    deviceClasses:
      - name: vg1
        default: true
        fstype: xfs
        deviceSelector:
          paths:                      # use stable by-path names, not /dev/nvmeXnY
            - /dev/disk/by-path/pci-0000:5a:00.0-nvme-1
            - /dev/disk/by-path/pci-0000:5b:00.0-nvme-1
        thinPoolConfig:
          name: thin-pool-1
          sizePercent: 90
          overprovisionRatio: 10
```

Verify:

```bash
oc get lvmcluster -n openshift-storage           # STATUS: Ready
oc get storageclass                              # lvms-vg1 (default)
oc get volumesnapshotclass                       # lvms-vg1
```

> [!NOTE]
> LVMS volumes are ReadWriteOnce and stay on the node where they were created. That is fine on a single node. For multi-node clusters use ODF CephFS or Trident; see [NetApp ONTAP and Trident](netapp_ontap.md).

OpenShift already runs a CSI snapshot controller (`openshift-cluster-storage-operator`). Do not install another one.

### 2. cert-manager

Install `openshift-cert-manager-operator` (namespace `cert-manager-operator`, channel `stable-v1`). The operator creates the `CertManager` singleton `cluster` and runs the cert-manager components in `cert-manager`.

```bash
oc get certmanager cluster -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}{"\n"}'
oc get pods -n cert-manager
```

The installer creates only namespaced `Issuer`/`Certificate` objects and an application `ClusterIssuer` named with its own prefix _(planned)_.

### 3. Service Mesh 3 (ambient mode)

The stack uses ambient-mode mTLS between its components. Install `servicemeshoperator3` (channel `stable`, all namespaces). Then create the control plane, CNI, and ztunnel. First read Red Hat's _Service Mesh 3 ambient mode_ prerequisites for your OpenShift version; some versions require cluster network settings.

```yaml
apiVersion: v1
kind: Namespace
metadata: { name: istio-system }
---
apiVersion: v1
kind: Namespace
metadata: { name: istio-cni }
---
apiVersion: v1
kind: Namespace
metadata: { name: ztunnel }
---
apiVersion: sailoperator.io/v1
kind: Istio
metadata:
  name: default
spec:
  namespace: istio-system
  profile: ambient
  values:
    pilot:
      trustedZtunnelNamespace: ztunnel
---
apiVersion: sailoperator.io/v1
kind: IstioCNI
metadata:
  name: default
spec:
  namespace: istio-cni
  profile: ambient
---
apiVersion: sailoperator.io/v1
kind: ZTunnel
metadata:
  name: default
spec:
  namespace: ztunnel
```

```bash
oc get istio,istiocni,ztunnel          # all READY / Healthy
```

> [!NOTE]
> OpenShift already runs its own istiod (`openshift-gateway-istiod` in `openshift-ingress`) for Gateway API. It is separate from the `istio-system` control plane you create here. Leave it alone.

### 4. Gateway API

Gateway API is part of OpenShift 4.19 and later. The CRDs and the `openshift-ingress` controller are managed by the Ingress Operator. Nothing to install.

```bash
oc get crd gateways.gateway.networking.k8s.io
oc get gatewayclass
```

Do not install the upstream Gateway API CRDs on top of these.

### 5. CloudNativePG

Install `cloudnative-pg` from **certified-operators** (channel `stable-v1`, namespace `openshift-operators`). It serves the same `postgresql.cnpg.io/v1` API the installer uses. The installer then creates only its own `Cluster` in the `postgresql` namespace _(planned: today the role installs the operator with Helm; see [Known gaps](#known-gaps))_.

```bash
oc get csv -n openshift-operators | grep cloudnative-pg      # Succeeded
oc get crd clusters.postgresql.cnpg.io
```

### 6. Red Hat build of Keycloak (when `auth_provider: keycloak`)

The RHBK operator supports only OwnNamespace/SingleNamespace. Install `rhbk-operator` (channel `stable-v26.6`) **into the namespace where the installer will create the Keycloak instance**, which by default is `keycloak`:

```yaml
apiVersion: v1
kind: Namespace
metadata: { name: keycloak }
---
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata: { name: keycloak, namespace: keycloak }
spec:
  targetNamespaces: [keycloak]
---
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata: { name: rhbk-operator, namespace: keycloak }
spec:
  name: rhbk-operator
  channel: stable-v26.6
  source: redhat-operators
  sourceNamespace: openshift-marketplace
  installPlanApproval: Manual
```

Do **not** create a `Keycloak` CR. The installer creates the instance, its database, and the realm.

Skip this step when `auth_provider: litellm`.

### 7. Custom Metrics Autoscaler

Model autoscaling uses KEDA. It replaces the Prometheus Adapter that the installer deploys on vanilla Kubernetes; on OpenShift the `custom.metrics.k8s.io` API must not be taken over. Install `openshift-custom-metrics-autoscaler-operator` (namespace `openshift-keda`), then:

```yaml
apiVersion: keda.sh/v1alpha1
kind: KedaController
metadata:
  name: keda
  namespace: openshift-keda
spec:
  watchNamespace: ""
```

```bash
oc get kedacontroller -n openshift-keda
oc get pods -n openshift-keda          # keda-operator, keda-metrics-apiserver Running
```

### 8. Node Feature Discovery

Install `nfd` (namespace `openshift-nfd`), then create the instance with the operator's default spec:

```yaml
apiVersion: nfd.openshift.io/v1
kind: NodeFeatureDiscovery
metadata:
  name: nfd-instance
  namespace: openshift-nfd
spec:
  operand: {}
```

```bash
oc get nodes -o json | jq '.items[].metadata.labels | with_entries(select(.key|startswith("feature.node.kubernetes.io/cpu-model")))'
```

The installer does not deploy NFD on OpenShift: with `nfd_enabled: true` (the default) it only checks that a `NodeFeatureDiscovery` exists and fails if it does not. The model catalog's `require_amx: true` schedules models only on nodes labelled `feature.node.kubernetes.io/cpu-cpuid.AMXBF16`, so without NFD every model stays Pending.

### 9. LeaderWorkerSet

Required by the OpenShift AI llm-d serving path (`LLMInferenceService`). Install `leader-worker-set` (namespace `openshift-lws-operator`, OwnNamespace, channel `stable-v1.0`), then:

```yaml
apiVersion: operator.openshift.io/v1
kind: LeaderWorkerSetOperator
metadata:
  name: cluster
spec:
  managementState: Managed
```

```bash
oc get crd leaderworkersets.leaderworkerset.x-k8s.io
```

### 10. Red Hat Connectivity Link

OpenShift AI serves `LLMInferenceService` (llm-d), which model-manager uses for every model, only when Red Hat Connectivity Link (Kuadrant) is installed. Without it the DataScienceCluster reports `KserveLLMInferenceServiceDependencies=False: Red Hat Connectivity Link is not installed, LLMInferenceService cannot be used`. Install it before creating the DataScienceCluster.

Install `rhcl-operator` (channel `stable`, all namespaces) into its own namespace. OLM adds the Authorino, Limitador, and DNS operators as dependencies. Then create the `Kuadrant` instance:

```yaml
apiVersion: v1
kind: Namespace
metadata: { name: kuadrant-system }
---
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata: { name: kuadrant, namespace: kuadrant-system }   # no targetNamespaces: all namespaces
---
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata: { name: rhcl-operator, namespace: kuadrant-system }
spec:
  name: rhcl-operator
  channel: stable
  source: redhat-operators
  sourceNamespace: openshift-marketplace
---
apiVersion: kuadrant.io/v1beta1
kind: Kuadrant
metadata: { name: kuadrant, namespace: kuadrant-system }
spec: {}
```

Apply the `Kuadrant` object once the `rhcl-operator` CSV is `Succeeded`.

```bash
oc get csv -n kuadrant-system                  # rhcl, authorino, limitador, dns operators Succeeded
oc get kuadrant kuadrant -n kuadrant-system    # Ready: Kuadrant is ready
```

### 11. OpenShift AI and the DataScienceCluster

This is two steps, and both are needed.

**a. Install the operator.** OpenShift AI is not part of OpenShift; install the `rhods-operator` package yourself. Its OperatorGroup watches all namespaces (no `targetNamespaces`):

```yaml
apiVersion: v1
kind: Namespace
metadata: { name: redhat-ods-operator }
---
apiVersion: operators.coreos.com/v1
kind: OperatorGroup
metadata: { name: rhods-operator, namespace: redhat-ods-operator }
---
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata: { name: rhods-operator, namespace: redhat-ods-operator }
spec:
  name: rhods-operator
  channel: stable-3.3                    # tested with rhods-operator.3.3.4
  source: redhat-operators
  sourceNamespace: openshift-marketplace
```

```bash
oc get csv -n redhat-ods-operator        # rhods-operator.3.3.x  Succeeded
oc get dsci                              # default-dsci  Ready (created by the operator)
```

**b. Create the `DataScienceCluster`.** Installing the operator is not enough. Out of the box it creates only `default-dsci` (namespaces, monitoring, CA bundle) and **no `DataScienceCluster`**, so no OpenShift AI component, including KServe, is running yet. Create one with **KServe managed**. Enable or disable the other components as you like; the stack does not need them.

```yaml
apiVersion: datasciencecluster.opendatahub.io/v2
kind: DataScienceCluster
metadata:
  name: default-dsc
spec:
  components:
    kserve:
      managementState: Managed
      rawDeploymentServiceConfig: Headless
    dashboard:       { managementState: Managed }
    workbenches:     { managementState: Removed }
    aipipelines:     { managementState: Removed }
    modelregistry:   { managementState: Removed }
    ray:             { managementState: Removed }
    kueue:           { managementState: Removed }
    trainingoperator: { managementState: Removed }
    trainer:         { managementState: Removed }
    trustyai:        { managementState: Removed }
    feastoperator:   { managementState: Removed }
    llamastackoperator: { managementState: Removed }
    mlflowoperator:  { managementState: Removed }
```

```bash
oc get dsc default-dsc -o jsonpath='{.status.phase}{"\n"}'        # Ready
oc get dsc default-dsc -o jsonpath='{.status.conditions[?(@.type=="KserveLLMInferenceServiceDependencies")].status}{"\n"}'   # True (needs step 10)
oc get crd | grep serving.kserve.io                                # inferenceservices, servingruntimes, llminferenceservices, ...
```

OpenShift AI owns the KServe CRDs and controller. On OpenShift the installer never deploys upstream KServe: the `kserve` component only checks that the `DataScienceCluster` has KServe `Managed` and ready, then adds the inference Gateway. Its teardown does nothing _(planned)_.

### 12. MetalLB

_Optional. Skip this on single-node clusters._

The stack is reached through the OpenShift router (`*.apps` wildcard domain), which already listens on the node's ports 80/443, so no `LoadBalancer` Service is needed. On single node, MetalLB can't help anyway: its only usable address is the node IP, and the router already holds those ports.

Install MetalLB only on a multi-node bare-metal cluster where you want a dedicated virtual IP that moves between nodes in front of a `LoadBalancer` Service. Install `metallb-operator` (namespace `metallb-system`, channel `stable`, AllNamespaces: omit `targetNamespaces` in the OperatorGroup), then create the instance, an address pool, and an L2 advertisement.

Pick a range of **free IPs on the node network** that DHCP does not hand out.

```yaml
apiVersion: metallb.io/v1beta1
kind: MetalLB
metadata:
  name: metallb
  namespace: metallb-system
---
apiVersion: metallb.io/v1beta1
kind: IPAddressPool
metadata:
  name: default-pool
  namespace: metallb-system
spec:
  addresses:
    - 10.0.0.240-10.0.0.250          # replace with your free range
---
apiVersion: metallb.io/v1beta1
kind: L2Advertisement
metadata:
  name: default-pool
  namespace: metallb-system
spec:
  ipAddressPools: [default-pool]
```

```bash
oc get pods -n metallb-system            # controller, speaker-* Running
oc get ipaddresspool -n metallb-system
```

### 13. Optional operators

| Need | Install | Instead of installer flag |
|---|---|---|
| Logs in Loki | `loki-operator` + `cluster-logging` (`LokiStack`, `ClusterLogForwarder`) | `observability_enabled` (Loki part) |
| Distributed tracing | `tempo-product` + `opentelemetry-product` (`TempoStack`, `OpenTelemetryCollector`) | `observability_enabled` (Tempo/OTel part) |
| Dashboards in the console | `cluster-observability-operator` (`UIPlugin`) | Grafana |
| Grafana | `grafana-operator` (community-operators, unsupported by Red Hat) | Grafana |
| CPU pinning for inference | Node Tuning Operator (built in) with a `PerformanceProfile` | `kubernetes_cpu_policy: nri-balloons` |

---

## Verify all prerequisites

Run this before the installer. Every line should end in `OK`. The installer's preflight check runs the same checks and stops on the first failure _(planned)_.

```bash
#!/usr/bin/env bash
# openshift-prereq-check.sh
ok()   { printf '%-45s OK\n' "$1"; }
fail() { printf '%-45s MISSING  %s\n' "$1" "$2"; rc=1; }
rc=0

csv() {   # csv <label> <namespace> <name-prefix>
  oc get csv -n "$2" -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.phase}{"\n"}{end}' 2>/dev/null \
    | grep -q "^$3.* Succeeded$" && ok "$1" || fail "$1" "operator CSV $3* not Succeeded in $2"
}
crd() { oc get crd "$2" >/dev/null 2>&1 && ok "$1" || fail "$1" "CRD $2"; }

oc get storageclass -o jsonpath='{.items[?(@.metadata.annotations.storageclass\.kubernetes\.io/is-default-class=="true")].metadata.name}' \
  | grep -q . && ok "default StorageClass" || fail "default StorageClass" "none marked default"

csv "cert-manager"               cert-manager-operator   cert-manager-operator
csv "Service Mesh 3"             openshift-operators     servicemeshoperator3
oc get istio default -o jsonpath='{.status.state}' 2>/dev/null | grep -q Healthy \
  && ok "Istio control plane (ambient)" || fail "Istio control plane (ambient)" "Istio/default not Healthy"
oc get ztunnel default >/dev/null 2>&1 && ok "ZTunnel" || fail "ZTunnel" "ZTunnel/default"
crd "Gateway API"                gateways.gateway.networking.k8s.io
csv "CloudNativePG"              openshift-operators     cloudnative-pg
csv "Red Hat build of Keycloak"  keycloak                rhbk-operator      # skip if auth_provider=litellm
oc get kedacontroller -n openshift-keda keda >/dev/null 2>&1 && ok "KEDA" || fail "KEDA" "KedaController/keda"
oc get nodefeaturediscovery -n openshift-nfd >/dev/null 2>&1 && ok "Node Feature Discovery" || fail "Node Feature Discovery" "NodeFeatureDiscovery instance"
crd "LeaderWorkerSet"            leaderworkersets.leaderworkerset.x-k8s.io
csv "Connectivity Link"          kuadrant-system         rhcl-operator
oc get kuadrant -n kuadrant-system -o jsonpath='{.items[0].status.conditions[?(@.type=="Ready")].status}' 2>/dev/null | grep -q True \
  && ok "Kuadrant" || fail "Kuadrant" "Kuadrant instance not Ready"
csv "OpenShift AI"               redhat-ods-operator     rhods-operator
oc get dsc -o jsonpath='{.items[0].status.phase}' 2>/dev/null | grep -q Ready \
  && ok "DataScienceCluster" || fail "DataScienceCluster" "no DSC in phase Ready"
crd "KServe (from OpenShift AI)" inferenceservices.serving.kserve.io
oc get dsc -o jsonpath='{.items[0].status.conditions[?(@.type=="KserveLLMInferenceServiceDependencies")].status}' 2>/dev/null | grep -q True \
  && ok "LLMInferenceService dependencies" || fail "LLMInferenceService dependencies" "see DSC condition KserveLLMInferenceServiceDependencies"

exit $rc
```

---
## Platform detection

Preflight detects the platform before it resolves the components to install, so the same `global_config.yaml` works on Kubernetes and OpenShift. There is no OpenShift-specific setting.

Only a cluster you bring (`existing_kubernetes` set) is probed. A cluster the installer builds with Kubespray is upstream Kubernetes. Preflight stops if the cluster in `existing_kubernetes` cannot be reached.

A cluster is OpenShift when it serves `config.openshift.io/v1` **and** has the `ClusterVersion` object `version`. The API group alone is not enough, because OKD and MicroShift serve parts of it as well.

| Fact | Value |
|---|---|
| `cluster_distribution` | `openshift` or `kubernetes` |
| `cluster_distribution_version` | the OpenShift version (`ClusterVersion` `status.desired.version`), or `''` |
| `cluster_kubernetes_version` | for example `v1.35.8`, or `''` when the cluster was not probed |
| `cluster_kubernetes_minor` | the minor version as an integer (`"34+"` becomes `34`), or `0` |
| `cluster_apis` | the served API group/versions, or `[]` |
| `cluster_default_storageclass` | the StorageClass marked `storageclass.kubernetes.io/is-default-class`, or `''` |

Preflight prints the result, for example:

```
Platform: openshift 4.22.17 (Kubernetes v1.35.8, 133 API group/versions)
```

To skip detection, set `cluster_distribution` in `global_config.yaml`. The other facts then fall back to the empty values above.

---

## Known gaps

The installer does not support all of the flow above yet. The blocking items stop a successful install today.

| Area | Today | Needed | Blocking |
|---|---|---|---|
| **Teardown safety** | Teardown roles delete CRDs by API group or regex (`kserve`, `envoy_gateway`, `observability`, `cert_manager`, `istio`, `metallb`, `keycloak`) and whole namespaces | An ownership label on everything the installer creates; teardown deletes only labelled objects and never CRDs it did not create | **Yes**: do not run teardown |
| **SCC** | Charts set fixed `runAsUser`/`fsGroup`; namespaces get only Pod Security labels, which do not grant an SCC on OpenShift | Leave UID/GID unset on OpenShift so `restricted-v2` assigns them; grant a dedicated SCC only where a component really needs one | **Yes** |
| **User-workload monitoring** | Not configured on OpenShift; `observability` deploys kube-prometheus-stack instead | When OpenShift is detected, merge `enableUserWorkload: true` into `cluster-monitoring-config` without overwriting the customer's settings, configure `user-workload-monitoring-config` with the StorageClass, and wait for `prometheus-user-workload` | **Yes** (no metrics, no autoscaling) |
| **Platform components** | Preflight detects OpenShift, but every component still installs as on Kubernetes | Skip what the cluster provides, check the marketplace operators from [Step-by-step](#step-by-step), and create only the instances | **Yes** |
| **Preflight checks** | Does not run the checks in [Verify all prerequisites](#verify-all-prerequisites) | Run those checks before the first component | No |

---

## Related Docs

| If you want to… | Go to |
|---|---|
| Install on a vanilla Kubernetes cluster you already run | [Multi-Node & BYO Cluster](topologies.md#bring-your-own-kubernetes-byo) |
| Check host and network prerequisites | [Prerequisites](../quickstart/prerequisites.md) |
| Set `storage_backend` and the other installer options | [Configuration Reference](../customize/configuration.md#storage) |
| Use NetApp ONTAP as the storage backend | [NetApp ONTAP and Trident](netapp_ontap.md) |
| Understand how a request reaches a model | [Network Architecture](networking.md) |
