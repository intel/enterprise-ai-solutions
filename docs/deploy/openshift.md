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
| **Cluster admin (you), from the marketplace** | Operators, plus the cluster-scoped singleton each one needs | LVMS + `LVMCluster`, cert-manager, Service Mesh 3 + `Istio`/`IstioCNI`/`ZTunnel`, CloudNativePG, Red Hat build of Keycloak, NFD + `NodeFeatureDiscovery`, LWS, Custom Metrics Autoscaler + `KedaController`, OpenShift AI + `DataScienceCluster` |
| **Installer (`es_auto_installer.sh`)** | Resources scoped to the application, in namespaces it creates and labels | Enabling user-workload monitoring _(planned)_, CNPG `Cluster`, `Keycloak` instance and realm, `ServiceMonitor`/`PodMonitor`, LiteLLM, Langfuse, inference models, eRAG applications |

The rule: **the installer never installs, upgrades, or deletes an operator, a CRD, or a cluster singleton that it did not create.** The one exception is user-workload monitoring: the installer switches it on by merging `enableUserWorkload: true` into `cluster-monitoring-config`, keeps any settings already there, and never switches it off. Anything the installer creates carries an ownership label, and teardown removes only labelled objects _(planned)_.

---

## Validated environment

Tested on 2026-10-16 with OpenShift Container Platform 4.22.17 on a single node (RHCOS, CRI-O), with the operator versions in [Prerequisites](#prerequisites). Multi-node clusters are expected to work if they have a ReadWriteMany StorageClass (ODF CephFS or Trident) and `storage_custom_rwx: true` ([step 1](#1-storage-default-storageclass)). They have not been validated yet.

---

## Prerequisites

Install each operator from the Software Catalog by following the Red Hat guide linked in the table. For the generic steps (Namespace, OperatorGroup, Subscription, approving the InstallPlan), see [Installing Operators in your namespace](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/operators/user-tasks#olm-installing-operators-in-namespace). The [steps below](#step-by-step) cover only what this stack needs beyond those guides.

To stay on the tested versions, subscribe to the channel in the table with `installPlanApproval: Manual`, so that OLM upgrades an operator only when you approve it. Other channels have not been tested. **Tested** is the installed CSV. **Catalog default** is what the OpenShift 4.22 catalog offered on the test date, when it differs.

| # | Component | Package (catalog) | Channel → tested | Catalog default | Red Hat guide | Required |
|---|---|---|---|---|---|---|
| 1 | Default StorageClass | `lvms-operator` (redhat-operators), or ODF / Trident | `stable-4.22` → 4.22.0 | | [LVM Storage](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/storage/persistent-storage-using-local-storage) | **Yes** |
| 2 | cert-manager | `openshift-cert-manager-operator` (redhat-operators) | `stable-v1` → 1.20.1 | | [cert-manager Operator](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/security_and_compliance/cert-manager-operator-for-red-hat-openshift) | **Yes** |
| 3 | Service Mesh 3 (ambient) | `servicemeshoperator3` (redhat-operators) | `stable` → 3.4.3 | | [Ambient mode](https://docs.redhat.com/en/documentation/red_hat_openshift_service_mesh/3.4/html/installing/ossm-istio-ambient-mode) | **Yes** |
| 4 | Gateway API | built in (Ingress Operator) | — | | [Gateway API](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/ingress_and_load_balancing/configuring-gateway-api) | **Yes** (nothing to install) |
| 5 | CloudNativePG | `cloudnative-pg` (certified-operators) | `stable-v1` → 1.30.1 | | [CloudNativePG](https://cloudnative-pg.io/documentation/current/installation_upgrade/) (certified, not a Red Hat product) | **Yes** |
| 6 | Red Hat build of Keycloak | `rhbk-operator` (redhat-operators) | `stable-v26.6` → 26.6.7-opr.1 | | [RHBK Operator installation](https://docs.redhat.com/en/documentation/red_hat_build_of_keycloak/26.6/html/operator_guide/installation-) | When `auth_provider: keycloak` |
| 7 | Custom Metrics Autoscaler | `openshift-custom-metrics-autoscaler-operator` (redhat-operators) | not installed in the test | `stable` → 2.19.0-4 | [Custom Metrics Autoscaler](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/nodes/automatically-scaling-pods-with-the-custom-metrics-autoscaler-operator) | For autoscaling _(planned)_ |
| 8 | Node Feature Discovery | `nfd` (redhat-operators) | `stable` → 4.22.0 | | [NFD Operator](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/specialized_hardware_and_driver_enablement/psap-node-feature-discovery-operator) | **Yes** |
| 9 | LeaderWorkerSet | `leader-worker-set` (redhat-operators) | `stable-v1.0` → 1.0.1 | | [LeaderWorkerSet Operator](https://docs.redhat.com/en/documentation/openshift_container_platform/4.22/html/ai_workloads/leader-worker-set-operator) | **Yes** (llm-d serving) |
| 10 | Red Hat Connectivity Link | `rhcl-operator` (redhat-operators) | `stable` → 1.4.3 | | [Install Connectivity Link](https://docs.redhat.com/en/documentation/red_hat_connectivity_link/1.4/html/install_connectivity_link/rhcl-install-on-ocp) | **Yes** (OpenShift AI `LLMInferenceService`) |
| 11 | OpenShift AI | `rhods-operator` (redhat-operators) | `stable-3.3` → 3.3.4 | `stable-3.x` → 3.5.1 (not tested) | [Installing OpenShift AI](https://docs.redhat.com/en/documentation/red_hat_openshift_ai_self-managed/3.3/html/installing_and_uninstalling_openshift_ai_self-managed/installing-and-deploying-openshift-ai_install) | **Yes** |

---

## Step-by-step

Each step lists only what the stack needs beyond the linked guide. The order does not matter, except that the DataScienceCluster (step 11) needs LeaderWorkerSet and the `Kuadrant` instance (steps 9 and 10) first. Everything must be in place before you run the installer.

### 1. Storage: default StorageClass

The installer needs a **default** StorageClass. It never creates, modifies, or demotes StorageClasses on OpenShift. LVM Storage (tested) is ReadWriteOnce only, which is fine on a single node. For multi-node clusters use ODF CephFS or Trident; see [NetApp ONTAP and Trident](netapp_ontap.md).

The installer cannot tell from a class whether it is ReadWriteMany, so it assumes ReadWriteOnce and stops on a cluster with more than one node. On a multi-node cluster with a ReadWriteMany default class, set `storage_custom_rwx: true` in `global_config.yaml`.

```bash
oc get storageclass                              # one marked (default)
```

OpenShift already runs a CSI snapshot controller. Do not install another one.

### 2. cert-manager

Nothing beyond the guide. The installer creates only namespaced `Issuer`/`Certificate` objects and an application `ClusterIssuer` named with its own prefix _(planned)_.

### 3. Service Mesh 3 (ambient mode)

Create the `Istio`, `IstioCNI` and `ZTunnel` objects for ambient mode as the guide describes, in `istio-system`, `istio-cni` and `ztunnel`.

```bash
oc get istio,istiocni,ztunnel          # all READY / Healthy
```

OpenShift already runs its own istiod (`openshift-gateway-istiod` in `openshift-ingress`) for Gateway API. It is separate from the `istio-system` control plane. Leave it alone.

### 4. Gateway API

Built into OpenShift 4.19 and later; the Ingress Operator owns the CRDs. Nothing to install, and do not install the upstream Gateway API CRDs on top.

### 5. CloudNativePG

Install `cloudnative-pg` from **certified-operators** into `openshift-operators` (all namespaces). It serves the same `postgresql.cnpg.io/v1` API the installer uses. The installer creates only its own `Cluster` in the `postgresql` namespace.

### 6. Red Hat build of Keycloak (when `auth_provider: keycloak`)

The operator watches only its own namespace, so install it **into `keycloak`**, the namespace where the installer creates the Keycloak instance. Do **not** create a `Keycloak` CR: the installer creates the instance, its database, and the realm.

### 7. Custom Metrics Autoscaler

Model autoscaling uses KEDA instead of the Prometheus Adapter that the installer deploys on vanilla Kubernetes; on OpenShift the `custom.metrics.k8s.io` API must not be taken over. Create the `KedaController` named `keda` in `openshift-keda`, as the guide describes.

### 8. Node Feature Discovery

Create a `NodeFeatureDiscovery` instance with the operator's default spec. With `nfd_enabled: true` (the default) the installer checks that one exists and fails if not. The model catalog's `require_amx: true` schedules models only on nodes labelled `feature.node.kubernetes.io/cpu-cpuid.AMXBF16`, so without NFD every model stays Pending.

### 9. LeaderWorkerSet

Required by OpenShift AI's llm-d serving path (`LLMInferenceService`). Create the `LeaderWorkerSetOperator` named `cluster` as the guide describes.

### 10. Red Hat Connectivity Link

Create the `Kuadrant` instance **before** the DataScienceCluster. Without it the DataScienceCluster reports `KserveLLMInferenceServiceDependencies=False`, and `LLMInferenceService`, which model-manager uses for every model, cannot be used.

```bash
oc get kuadrant -A                     # Ready: Kuadrant is ready
```

### 11. OpenShift AI and the DataScienceCluster

Installing the operator is not enough: it creates no `DataScienceCluster`, so no component, including KServe, runs yet. Create one with **KServe managed**. The other components are up to you; the stack does not need them. Tested with:

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
    dashboard: { managementState: Managed }
    # every other component: Removed
```

```bash
oc get dsc default-dsc -o jsonpath='{.status.phase}{"\n"}'        # Ready
oc get dsc default-dsc -o jsonpath='{.status.conditions[?(@.type=="KserveLLMInferenceServiceDependencies")].status}{"\n"}'   # True (needs step 10)
```

OpenShift AI owns the KServe CRDs and controller. On OpenShift the installer never deploys upstream KServe: the `kserve` component only checks that the `DataScienceCluster` has KServe `Managed` and ready, then adds the inference Gateway. Its teardown does nothing _(planned)_.

---

## Platform detection

Preflight detects the platform before it resolves the components to install. Components that OpenShift does not need (`istio`, `metallb`) are skipped there whatever `global_config.yaml` says. The installer never stamps Pod Security labels on OpenShift, whatever `enforce_pss` says: the platform syncs them from the SCCs, and the keycloak namespace is the admin's. One default in `configs/defaults/global_config.yaml` is for Kubernetes. Set it in `env/<env>/global_config.yaml` of an OpenShift env:

```yaml
istio_enabled: false   # true makes roles label their namespaces for ambient mode, which enrolls them in Service Mesh 3
```

Only a cluster you bring (`existing_kubernetes` set) is probed. A cluster the installer builds with Kubespray is upstream Kubernetes. Preflight stops if the cluster in `existing_kubernetes` cannot be reached.

The installer does not install OpenShift: set `existing_kubernetes` to the cluster's kubeconfig, or preflight stops. As on any BYO cluster, `kubernetes` leaves the cluster alone. `storage` uses the cluster's default StorageClass (step 1) or the class in `storage_custom_storageclass`, whose CSI driver comes from the marketplace.

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

To skip detection, set `cluster_distribution` in `global_config.yaml` to `openshift` or `kubernetes`; preflight stops on any other value. `cluster_default_storageclass` is still read; the other facts fall back to the empty values above.

---

## Known gaps

The installer does not support all of the flow above yet. The blocking items stop a successful install today.

| Area | Today | Needed | Blocking |
|---|---|---|---|
| **Teardown safety** | Teardown roles delete CRDs by API group or regex (`kserve`, `envoy_gateway`, `observability`, `cert_manager`, `istio`, `metallb`, `keycloak`) and whole namespaces | An ownership label on everything the installer creates; teardown deletes only labelled objects and never CRDs it did not create | **Yes**: do not run teardown |
| **SCC** | Charts set fixed `runAsUser`/`fsGroup`; namespaces get only Pod Security labels, which do not grant an SCC on OpenShift | Leave UID/GID unset on OpenShift so `restricted-v2` assigns them; grant a dedicated SCC only where a component really needs one | **Yes** |
| **User-workload monitoring** | Not configured on OpenShift; `observability` deploys kube-prometheus-stack instead | When OpenShift is detected, merge `enableUserWorkload: true` into `cluster-monitoring-config` without overwriting the customer's settings, configure `user-workload-monitoring-config` with the StorageClass, and wait for `prometheus-user-workload` | **Yes** (no metrics, no autoscaling) |
| **Platform components** | Preflight detects OpenShift, but every component still installs as on Kubernetes | Skip what the cluster provides, check the marketplace operators from [Prerequisites](#prerequisites), and create only the instances | **Yes** |
| **CPU pinning (NRI balloons) and tensor parallelism** | OpenShift AI cannot annotate the pods of an `LLMInferenceService`, so every model pod (`kserve.io/component=workload`) lands in the `vllm-balloon-tp1` balloon and gets the CPUs of one NUMA node. Only TP=1 models are supported | Pick the balloon by the model's TP size | No (TP>1 models run with too few CPUs) |
| **Preflight checks** | Does not check the operators in [Prerequisites](#prerequisites) | Check them before the first component | No |
