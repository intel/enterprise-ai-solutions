# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Which of a node's pods a DaemonSet owns, and which are not Ready.

    {{ pod_info.resources | daemonset_pods | unready_pods }}

pod_info is a k8s_info result for the pods on one node.
"""


def daemonset_pods(pods):
    """The pods a DaemonSet owns."""
    return [pod for pod in pods or []
            if any(owner.get("kind") == "DaemonSet"
                   for owner in (pod.get("metadata") or {}).get("ownerReferences") or [])]


def unready_pods(pods):
    """Names of the pods whose Ready condition is not True."""
    return [(pod.get("metadata") or {}).get("name") for pod in pods or []
            if not any(cond.get("type") == "Ready" and cond.get("status") == "True"
                       for cond in (pod.get("status") or {}).get("conditions") or [])]


class FilterModule:
    def filters(self):
        return {"daemonset_pods": daemonset_pods, "unready_pods": unready_pods}
