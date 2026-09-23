from __future__ import annotations

from types import SimpleNamespace

import pytest

from automation_harness.core.resolution_retry import install_component_handle_retry, object_resolution_timeout


def test_resolution_timeout_defaults_to_five_seconds(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_TIMEOUT", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert object_resolution_timeout() == 5.0


def test_resolution_timeout_environment_override(monkeypatch):
    monkeypatch.setenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_TIMEOUT", "8.5")
    assert object_resolution_timeout() == 8.5


def test_component_resolution_retries_until_success(monkeypatch):
    monkeypatch.setenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_TIMEOUT", "0.2")
    monkeypatch.setenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_RETRY_INTERVAL", "0.001")

    class Evidence:
        def record(self, *_args, **_kwargs):
            pass

    class Handle:
        _resolution_retry_installed = False

        def __init__(self):
            self.context = SimpleNamespace(evidence=Evidence())
            self.definition = SimpleNamespace(component_id="target")
            self.calls = 0

        def resolve(self):
            self.calls += 1
            if self.calls < 3:
                raise LookupError("not present yet")
            return "resolved"

        def state(self):
            return "state"

    install_component_handle_retry(Handle)
    handle = Handle()

    assert handle.resolve() == "resolved"
    assert handle.calls == 3


def test_zero_resolution_timeout_disables_retry(monkeypatch):
    monkeypatch.setenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_TIMEOUT", "0")

    class Evidence:
        def record(self, *_args, **_kwargs):
            pass

    class Handle:
        _resolution_retry_installed = False

        def __init__(self):
            self.context = SimpleNamespace(evidence=Evidence())
            self.definition = SimpleNamespace(component_id="target")
            self.calls = 0

        def resolve(self):
            self.calls += 1
            raise LookupError("missing")

        def state(self):
            return "state"

    install_component_handle_retry(Handle)
    handle = Handle()

    with pytest.raises(LookupError, match="missing"):
        handle.resolve()
    assert handle.calls == 1
