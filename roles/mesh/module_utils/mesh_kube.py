# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""The kubernetes client for the mesh modules: loading, the no_proxy fix and errors."""

import json
import os

try:
    from kubernetes import client, config
    from kubernetes.client.rest import ApiException
    from kubernetes.config import ConfigException
    from urllib3.exceptions import HTTPError
except ImportError:  # reported by run()
    client = None
    ConfigException = HTTPError = Exception

    class ApiException(Exception):
        def __init__(self, status=None):
            super().__init__(status)
            self.status = status


def run(module, what, call):
    """exit_json with call(api client)'s result; fail_json with `<what> failed: ...`."""
    if client is None:
        module.fail_json(msg="the kubernetes Python client is required")
    kubeconfig = os.environ.get("K8S_AUTH_KUBECONFIG") or os.environ.get("KUBECONFIG") or "~/.kube/config"
    try:
        cfg = client.Configuration()
        config.load_kube_config(config_file=kubeconfig, client_configuration=cfg)
        # The client reads http(s)_proxy from the environment but not no_proxy.
        cfg.no_proxy = os.environ.get("K8S_AUTH_NO_PROXY") or os.environ.get("no_proxy") or os.environ.get("NO_PROXY")
        module.exit_json(**call(client.ApiClient(cfg)))
    except ApiException as e:
        try:
            reason = json.loads(e.body).get("message", e.reason)
        except (TypeError, ValueError):
            reason = e.reason
        module.fail_json(msg="%s failed: %s %s" % (what, e.status, reason))
    except (ConfigException, HTTPError) as e:  # no kubeconfig, API server unreachable
        module.fail_json(msg="%s failed: %s" % (what, e))
