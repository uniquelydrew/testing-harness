from __future__ import annotations

from types import SimpleNamespace

import pytest

from automation_harness.authoring import atspi_click_capture_policy as capture_policy
from automation_harness.core.resolution_retry import install_component_handle_retry, object_resolution_timeout
from automation_harness.models.component import CapturedComponent, ComponentState, ComponentStrategy


def _captured(framework=None, pid=None):
    authored = None
    properties = {}
    if framework == "javafx":
        authored = ComponentStrategy("javafx", {"identification": {"mandatory": {"id": "target"}}})
        properties["bridge_pid"] = pid
    elif pid is not None:
        properties["process_id"] = pid
    return CapturedComponent(
        name="Target",
        role="button",
        description=None,
        accessible_id="target",
        application="Target Application",
        window="Target Application",
        hierarchy=(),
        actions=("click",),
        bounds=(10, 20, 40, 20),
        state=ComponentState(present=True, visible=True, showing=True, enabled=True),
        backend_properties=properties,
        authored_strategy=authored,
        framework=framework,
        native_class="javafx.scene.control.Button" if framework == "javafx" else None,
    )


class _JavaFxPointDriver:
    available = True

    def __init__(self):
        self.calls = []

    def capture_at_point(self, x, y, *, process_id=None):
        self.calls.append((x, y, process_id))
        if process_id != 4321:
            raise LookupError("wrong process")
        return _captured("javafx", 4321)


class _AtspiPointDriver:
    available = True

    def __init__(self):
        self.called = False

    def capture_at_point_snapshot(self, x, y, *, excluded_application_prefixes=()):
        self.called = True
        return _captured(None, 4321)

    def capture_at_point(self, x, y):
        self.called = True
        return _captured(None, 4321)


def test_point_capture_prefers_javafx_for_x11_owning_process(monkeypatch):
    javafx = _JavaFxPointDriver()
    atspi = _AtspiPointDriver()
    service = SimpleNamespace(javafx_driver=javafx, driver=atspi)
    monkeypatch.setattr(capture_policy, "_owner_pid_at_current_pointer", lambda x, y: 4321)

    result = capture_policy._resolve_hybrid_point(service, (100, 200), scoped=True)

    assert result.framework == "javafx"
    assert javafx.calls == [(100, 200, 4321)]
    assert atspi.called is False


def test_point_capture_promotes_atspi_process_to_javafx_when_owner_unknown(monkeypatch):
    javafx = _JavaFxPointDriver()
    atspi = _AtspiPointDriver()
    service = SimpleNamespace(javafx_driver=javafx, driver=atspi)
    monkeypatch.setattr(capture_policy, "_owner_pid_at_current_pointer", lambda x, y: None)

    result = capture_policy._resolve_hybrid_point(service, (100, 200), scoped=True)

    assert result.framework == "javafx"
    assert atspi.called is True
    assert javafx.calls[0] == (100, 200, 4321)


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
