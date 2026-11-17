# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for mesh_kube.run: each error ends in fail_json.

    python -m pytest roles/mesh/tests
"""

from types import SimpleNamespace as NS

import pytest

import mesh_kube


class Done(BaseException):
    """exit_json / fail_json end the module, as in Ansible (SystemExit): an except Exception
    in the module must not catch it."""


class Module:
    def __init__(self):
        self.result = self.failed = None

    def exit_json(self, **result):
        self.result = result
        raise Done

    def fail_json(self, msg):
        self.failed = msg
        raise Done


def api_error(status, reason, body):
    e = mesh_kube.ApiException(status=status)
    e.status, e.reason, e.body = status, reason, body
    return e


@pytest.fixture
def kube(monkeypatch):
    """A fake client and config; kube.load is what load_kube_config does."""
    state = NS(load=lambda **kw: None, configs=[])
    monkeypatch.setattr(mesh_kube, "client", NS(Configuration=lambda: NS(), ApiClient=lambda cfg: cfg))
    monkeypatch.setattr(mesh_kube, "config", NS(load_kube_config=lambda **kw: state.load(**kw)), raising=False)
    monkeypatch.delenv("K8S_AUTH_NO_PROXY", raising=False)
    monkeypatch.setenv("no_proxy", ".cluster.local")
    return state


def run(call):
    module = Module()
    with pytest.raises(Done):
        mesh_kube.run(module, "mesh policies", call)
    return module


def test_result_and_no_proxy(kube):
    module = run(lambda api: {"changed": True, "no_proxy": api.no_proxy})
    assert module.result == {"changed": True, "no_proxy": ".cluster.local"}
    assert module.failed is None


def test_api_error_message_from_body(kube):
    def call(api):
        raise api_error(403, "Forbidden", '{"message": "namespaces is forbidden"}')
    assert run(call).failed == "mesh policies failed: 403 namespaces is forbidden"


def test_api_error_without_json_body_uses_reason(kube):
    def call(api):
        raise api_error(500, "Internal Server Error", "<html>oops</html>")
    assert run(call).failed == "mesh policies failed: 500 Internal Server Error"


def test_api_error_without_body_uses_reason(kube):
    def call(api):
        raise api_error(404, "Not Found", None)
    assert run(call).failed == "mesh policies failed: 404 Not Found"


@pytest.mark.parametrize("error", [mesh_kube.ConfigException("no kubeconfig"), mesh_kube.HTTPError("unreachable")])
def test_config_and_connection_errors(kube, error):
    def load(**kw):
        raise error
    kube.load = load
    module = run(lambda api: pytest.fail("called without a config"))
    assert module.failed == "mesh policies failed: %s" % error


def test_connection_error_in_call(kube):
    def call(api):
        raise mesh_kube.HTTPError("connection refused")
    assert run(call).failed == "mesh policies failed: connection refused"


def test_no_client(monkeypatch):
    monkeypatch.setattr(mesh_kube, "client", None)
    module = run(lambda api: pytest.fail("called without a client"))
    assert module.failed == "the kubernetes Python client is required"
