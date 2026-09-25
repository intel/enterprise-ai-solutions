#!/bin/bash
# Copyright (C) 2025-2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0

###############################################################################
# Script: calculate_actual_infrastructure_cpus.sh
# Description: Calculate ACTUAL CPU usage from running infrastructure pods
#              Does NOT use hardcoded profiles - calculates from reality
# Usage: ./calculate_actual_infrastructure_cpus.sh
###############################################################################

set -euo pipefail

# Infrastructure namespaces (not model workloads)
# Scoped to the actual stack: kubernetes + envoy-gateway + cert-manager +
# keycloak + kserve + optional observability/storage.
# Removed: auth-apisix, flowise, istio-system, rook-ceph, ingress-nginx
# (not part of this stack).
INFRA_NAMESPACES=(
    "kube-system"
    "default"
    "cert-manager"
    "envoy-gateway-system"
    "keycloak"
    "kserve"
    "monitoring"      # Observability (optional)
)

# Model workload namespaces (EXCLUDE these)
MODEL_NAMESPACES=(
    "vllm"
    "tgi"
    "ovms"
)

echo "[INFO] Calculating actual infrastructure CPU usage..."
echo ""

# Get all running pods with CPU requests
TOTAL_MILLICORES=0

for ns in "${INFRA_NAMESPACES[@]}"; do
    # Check if namespace exists
    if ! kubectl get namespace "$ns" >/dev/null 2>&1; then
        continue
    fi

    # Get CPU requests for this namespace
    NS_MILLICORES=$(kubectl get pods -n "$ns" -o json 2>/dev/null | jq -r '
        [.items[] |
         select(.status.phase=="Running") |
         .spec.containers[].resources.requests.cpu // "0m"] |
        map(
            if endswith("m") then
                rtrimstr("m") | tonumber
            elif tonumber > 0 then
                tonumber * 1000
            else
                0
            end
        ) | add // 0
    ')

    if [ "$NS_MILLICORES" -gt 0 ]; then
        echo "[INFO] $ns: ${NS_MILLICORES}m"
        TOTAL_MILLICORES=$((TOTAL_MILLICORES + NS_MILLICORES))
    fi
done

echo ""
echo "[INFO] Total infrastructure CPU requests: ${TOTAL_MILLICORES}m"

# Convert to full cores (round up)
TOTAL_CORES=$(( (TOTAL_MILLICORES + 999) / 1000 ))

echo "[INFO] Rounded to full cores: ${TOTAL_CORES} CPUs"

# Add safety buffer (20% overhead for bursts)
RESERVED_CPUS=$(( TOTAL_CORES + (TOTAL_CORES / 5) ))

# Round to even number (NUMA alignment)
if [ $((RESERVED_CPUS % 2)) -ne 0 ]; then
    RESERVED_CPUS=$((RESERVED_CPUS + 1))
fi

# Minimum 4 CPUs
if [ "$RESERVED_CPUS" -lt 4 ]; then
    RESERVED_CPUS=4
fi

echo "[INFO] Reserved CPUs (with 20% buffer): ${RESERVED_CPUS} CPUs"
echo ""
echo "INFRASTRUCTURE_RESERVED_CPUS=${RESERVED_CPUS}"
