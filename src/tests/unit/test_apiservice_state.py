# Copyright (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
# Run: python3 -m pytest src/tests/unit
import importlib.util
import pathlib

_PLUGIN = pathlib.Path(__file__).resolve().parents[3] / "filter_plugins" / "apiservice_state.py"
_spec = importlib.util.spec_from_file_location("apiservice_state", _PLUGIN)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
apiservice_available = _mod.apiservice_available
apiservice_state = _mod.apiservice_state

SERVICE = {"namespace": "keda", "name": "keda-operator-metrics-apiserver", "port": 443}
RELEASE = {"chart": "keda-2.21.0", "status": "deployed"}


def api(available):
    return [{"spec": {"service": SERVICE},
             "status": {"conditions": [{"type": "Available", "status": "True" if available else "False"}]}}]


def test_available_reads_the_condition():
    assert apiservice_available(api(True)) is True
    assert apiservice_available(api(False)) is False
    assert apiservice_available([{"status": {}}]) is False
    assert apiservice_available([]) is False
    assert apiservice_available(None) is False


def test_not_registered():
    assert apiservice_state([], None) == {
        "ours": False, "available": False, "foreign": False, "stale": False, "service": {}}


def test_ours_is_never_foreign_or_stale():
    for up in (True, False):
        state = apiservice_state(api(up), RELEASE)
        assert (state["ours"], state["foreign"], state["stale"]) == (True, False, False)
        assert state["available"] is up


def test_another_install_answering_is_foreign():
    state = apiservice_state(api(True), None)
    assert (state["foreign"], state["stale"], state["service"]) == (True, False, SERVICE)


def test_another_install_down_is_stale():
    state = apiservice_state(api(False), None)
    assert (state["foreign"], state["stale"]) == (False, True)


def test_missing_service_spec():
    assert apiservice_state([{"status": {}}], None)["service"] == {}
