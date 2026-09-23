import pytest

from automation_harness.core.routed_capture import RoutedCaptureService
from automation_harness.core.runtime_observation import Framework
from automation_harness.core.technology_router import AdapterKind, TargetContext
from automation_harness.models.component import CapturedComponent, ComponentState


def _capture(*, framework="swing", pid=42, window="Main"):
    native_class = (
        "javafx.scene.control.Button"
        if framework == "javafx" else "javax.swing.JButton"
    )
    return CapturedComponent(
        name="Run", role="button", description=None, accessible_id="run",
        application="MSCT", window=window, hierarchy=(), actions=("click",),
        bounds=(1, 2, 30, 20), state=ComponentState(present=True),
        backend_properties={"process_id": pid}, framework=framework,
        native_class=native_class,
    )


class _Adapter:
    available = True

    def __init__(self, captured=None, error=None):
        self.captured = captured
        self.error = error
        self.calls = 0

    def capture_next_click(self, *, timeout):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.captured


def test_only_routed_java_adapter_observes_the_click():
    java = _Adapter(_capture())
    javafx = _Adapter(_capture(framework="javafx"))
    atspi = _Adapter(_capture(framework=None))
    service = RoutedCaptureService({
        AdapterKind.JAVA_AGENT: java,
        AdapterKind.JAVAFX: javafx,
        AdapterKind.ATSPI: atspi,
    })

    result = service.capture(TargetContext(
        process_id=42, window_id="Main", framework_hint=Framework.SWING,
        java_agent_pids=frozenset({42}), javafx_pids=frozenset({42}),
    ))

    assert result.observation.framework is Framework.JAVAFX
    assert result.observation.evidence.adapter == "javafx"
    assert java.calls == 0
    assert javafx.calls == 1
    assert atspi.calls == 0


def test_explicit_swing_fallback_records_degraded_evidence():
    java = _Adapter(error=RuntimeError("agent disconnected"))
    atspi = _Adapter(_capture(framework=None))
    result = RoutedCaptureService({
        AdapterKind.JAVA_AGENT: java,
        AdapterKind.ATSPI: atspi,
    }).capture(TargetContext(
        process_id=42, window_id="Main", framework_hint=Framework.SWING,
        java_agent_pids=frozenset({42}),
    ))

    assert result.observation.evidence.fallback_used is True
    assert result.observation.evidence.authoritative is False
    assert "agent disconnected" in result.observation.evidence.reasons[-1]


def test_wrong_process_capture_is_rejected():
    java = _Adapter(_capture(pid=99))
    service = RoutedCaptureService({AdapterKind.JAVA_AGENT: java})
    with pytest.raises(LookupError, match="resolved process 99"):
        service.capture(TargetContext(
            process_id=42, window_id="Main", framework_hint=Framework.SWING,
            java_agent_pids=frozenset({42}),
        ))


def test_wrong_window_capture_is_rejected():
    java = _Adapter(_capture(window="Other"))
    service = RoutedCaptureService({AdapterKind.JAVA_AGENT: java})
    with pytest.raises(LookupError, match="resolved window"):
        service.capture(TargetContext(
            process_id=42, window_id="Main", framework_hint=Framework.SWING,
            java_agent_pids=frozenset({42}),
        ))
