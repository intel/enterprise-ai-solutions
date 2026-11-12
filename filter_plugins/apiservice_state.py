# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Who serves an aggregated API, and whether it answers.

    {{ api_info.resources | apiservice_available }}
    {{ api_info.resources | apiservice_state(release_info.status | default(none)) }}

api_info is a k8s_info result for one APIService; the release is ours when its
Helm status is set. Another install's API is foreign when it answers and stale
when it is registered but down: both are left alone.
"""


def apiservice_available(resources):
    """True when the first APIService reports Available=True."""
    for api in (resources or [])[:1]:
        for cond in (api.get("status") or {}).get("conditions") or []:
            if cond.get("type") == "Available" and cond.get("status") == "True":
                return True
    return False


def apiservice_state(resources, release_status):
    api = (resources or [None])[0]
    available = apiservice_available(resources)
    ours = release_status is not None
    return {
        "ours": ours,
        "available": available,
        "foreign": not ours and available,
        "stale": not ours and api is not None and not available,
        "service": ((api or {}).get("spec") or {}).get("service") or {},
    }


class FilterModule:
    def filters(self):
        return {"apiservice_available": apiservice_available, "apiservice_state": apiservice_state}
