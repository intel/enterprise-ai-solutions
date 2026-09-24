#!/bin/bash
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

# Install the snapshot.storage.k8s.io CRDs and the snapshot controller by hand.
#
# FALLBACK ONLY. The storage component installs both itself, so a cluster whose
# storage was installed by this repo needs nothing from this script — run
# `./es_auto_installer.sh install storage --env <env>` instead, and
# `validate storage` to check the result.
#
# Use it on a cluster whose CSI driver was installed some other way and predates
# snapshot support, and which is not being reinstalled. Two consequences of doing
# it this way:
#
#   * everything created here is owned by kubectl, not by Helm or by Ansible, so
#     no teardown path removes it;
#   * the versions are pinned below rather than derived from the CSI driver's
#     chart, so they can drift from what the driver expects.
#
# The VolumeSnapshotClass is deliberately NOT created here. The velero component
# creates or adopts one for the cluster's CSI driver and applies its own label
# (see roles/velero/tasks/snapshot_class.yaml). Snapshots for anything else need
# a class naming that driver; ask the driver's own documentation for the
# parameters it takes.
#
# Usage: KUBECONFIG=<path> ./scripts/enable-csi-snapshots.sh
set -euo pipefail

: "${KUBECONFIG:?Set KUBECONFIG to the target cluster}"

SNAPSHOT_VERSION="v8.2.0"
SNAPSHOT_BASE="https://raw.githubusercontent.com/kubernetes-csi/external-snapshotter/${SNAPSHOT_VERSION}"

echo "=== Installing VolumeSnapshot CRDs (${SNAPSHOT_VERSION}) ==="
kubectl apply -f "${SNAPSHOT_BASE}/client/config/crd/snapshot.storage.k8s.io_volumesnapshotclasses.yaml"
kubectl apply -f "${SNAPSHOT_BASE}/client/config/crd/snapshot.storage.k8s.io_volumesnapshotcontents.yaml"
kubectl apply -f "${SNAPSHOT_BASE}/client/config/crd/snapshot.storage.k8s.io_volumesnapshots.yaml"

echo ""
echo "=== Installing snapshot-controller ==="
kubectl apply -f "${SNAPSHOT_BASE}/deploy/kubernetes/snapshot-controller/rbac-snapshot-controller.yaml"
kubectl apply -f "${SNAPSHOT_BASE}/deploy/kubernetes/snapshot-controller/setup-snapshot-controller.yaml"

echo ""
echo "=== Waiting for snapshot-controller to be ready ==="
kubectl rollout status deployment/snapshot-controller -n kube-system --timeout=120s

echo ""
echo "=== Verification ==="
kubectl get crd | grep snapshot.storage.k8s.io
kubectl -n kube-system get deploy snapshot-controller
echo ""
echo "Done. VolumeSnapshot objects will now be serviced."
echo "Next: install the backup component, which creates or adopts a"
echo "VolumeSnapshotClass for this cluster's CSI driver."
