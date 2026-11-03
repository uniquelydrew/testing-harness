"""Window/dialog close semantics must preserve native lifecycle and target identity."""
from types import SimpleNamespace

import pytest

from automation_harness.authoring.action_catalog import action_by_id, actions_for
from automation_harness.core.component_handle import ComponentHandle, UnsupportedGuiAction
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.interaction_preparation import preparation_requirement
from automation_harness.core.test_plan import validate_plan_components
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver, JavaFxBridgeProtocolError
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.models.plan import TestPlan


@pytest.mark.parametrize("kind", (ObjectType.WINDOW, ObjectType.DIALOG))
def test_legacy_window_exposes_canonical_parameterless_close(kind):
    definition = ComponentDefinition(
        "Advanced Details Window", object_type=kind,
        actions=frozenset({"resolve", "activate", "focus"}),
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"class": "edu.mit.ll.ersa.common.dashboard.components.AdvancedDetailsDisplay"},
                               "assistive": {"window": "Advanced Details"}}
        }),),
    )
    assert definition.supports(ActionType.CLOSE)
    action = action_by_id(definition, "close")
    assert not action.inputs
    call = action.to_step_call("close-details", definition.object_id)
    assert call.inputs == {
        "component_id": definition.object_id,
        "action": {"type": "close"},
    }
    assert not validate_plan_components(
        TestPlan("close-details", steps=(call,)),
        ComponentRepository({definition.component_id: definition}),
    )


@pytest.mark.parametrize("kind", (
    ObjectType.PANEL, ObjectType.TAB_CONTAINER, ObjectType.TAB,
    ObjectType.SPLIT_CONTAINER, ObjectType.SCROLL_CONTAINER,
))
def test_non_window_containers_do_not_offer_close(kind):
    component = ComponentDefinition(
        "container", object_type=kind, actions=frozenset({"click", "focus"}),
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "container"}}
        }),),
    )
    assert not any(action.action_id == "close" for action in actions_for(component))
    assert not component.supports(ActionType.CLOSE)


@pytest.mark.parametrize("kind", (ObjectType.WINDOW, ObjectType.DIALOG))
def test_window_close_is_semantic_not_pointer_or_focus(kind, monkeypatch):
    definition = ComponentDefinition(
        "Advanced Details Window", object_type=kind,
        actions=frozenset({"close", "activate"}),
        strategies=(ComponentStrategy("javafx", {"identification": {
            "mandatory": {"class": "advanced.Details"},
            "assistive": {"window": "Advanced Details"},
        }}),),
    )
    context = SimpleNamespace(
        backend="live-desktop",
        evidence=SimpleNamespace(record=lambda *_args, **_kw: None),
    )
    handle = ComponentHandle(context, definition)
    called = []
    monkeypatch.setattr(ComponentHandle, "_accessibility_operation",
                        lambda _self, label, method, *args, **kwargs:
                        called.append((label, method, args, kwargs))
                        or {"closed": True, "window": "Advanced Details"})
    class NativeWindow:
        def __init__(self, _ctx):
            pass
        def activate_window(self, **kwargs):
            called.append(("activate_window", kwargs))
            return {"window": "Advanced Details", "focused": True}
    monkeypatch.setattr(
        "automation_harness.core.component_handle.JavaFxBridgeDriver", NativeWindow,
    )
    preparation = preparation_requirement(ActionType.CLOSE, object_type=kind)
    assert preparation.activate_window
    assert preparation.require_window_activation
    assert not preparation.request_focus
    result = handle.execute("close")
    assert result.result["closed"] is True
    assert called[0][0] == "activate_window"
    assert called[1:] == [("close window", "close_window", (), {"fail_closed": True})]


def test_javafx_close_uses_unique_explicit_stage_without_resolving_child():
    class Endpoint:
        pid = 4442
        agent_sha256 = "fixture"
        def __init__(self):
            self.calls = []
        def request(self, op, **kwargs):
            self.calls.append((op, kwargs))
            if op == "windows":
                return {"windows": [{"title": "Advanced Details", "showing": True}]}
            assert op == "close_window"
            assert kwargs["window"] == "Advanced Details"
            return {"closed": True, "window": "Advanced Details",
                    "mechanism": "javafx.window_close_request"}
    endpoint = Endpoint()
    driver = JavaFxBridgeDriver()
    driver.endpoints = lambda: (endpoint,)
    result = driver.close_window(identification={"assistive": {"window": "Advanced Details"}})
    assert result["closed"] is True
    assert [op for op, _ in endpoint.calls] == ["windows", "close_window"]


def test_javafx_close_never_guesses_stage_or_accepts_missing_acknowledgement():
    class Endpoint:
        pid = 4442
        def request(self, op, **kwargs):
            if op == "windows":
                return {"windows": [{"title": "Advanced Details", "showing": True}]}
            return {"window": "Advanced Details", "closed": False}
    driver = JavaFxBridgeDriver()
    driver.endpoints = lambda: (Endpoint(),)
    with pytest.raises(ValueError, match="explicit owning-window"):
        driver.close_window(identification={"mandatory": {"class": "advanced.Details"}})
    with pytest.raises(JavaFxBridgeProtocolError, match="did not confirm"):
        driver.close_window(window="Advanced Details")


def test_javafx_close_refuses_ambiguous_window_titles():
    class Endpoint:
        pid = 4442
        def request(self, op, **kwargs):
            assert op == "windows"
            return {"windows": [
                {"title": "Advanced Details", "showing": True},
                {"title": "Advanced Details", "showing": True},
            ]}
    driver = JavaFxBridgeDriver()
    driver.endpoints = lambda: (Endpoint(),)
    with pytest.raises(LookupError, match="ambiguous"):
        driver.close_window(window="Advanced Details")


def test_close_source_contract_preserves_aut_lifecycle_events():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    javafx = (root / "javafx_agent/src/main/java/com/automationharness/javafx/AutomationHarnessJavaFxAgent.java").read_text(encoding="utf-8")
    swing = (root / "java-agent/src/java8/java/automation/harness/agent/SwingRecorder.java").read_text(encoding="utf-8")
    server = (root / "java-agent/src/java8/java/automation/harness/agent/AgentServer.java").read_text(encoding="utf-8")
    assert '"close_window".equals(op)' in javafx
    assert 'WINDOW_CLOSE_REQUEST' in javafx
    assert 'isShowing", false' in javafx
    assert 'WindowEvent.WINDOW_CLOSING' in swing
    assert 'window.dispatchEvent(' in swing
    assert '"/close_window"' in server


@pytest.mark.parametrize("kind", (ObjectType.WINDOW, ObjectType.DIALOG))
def test_reference_only_window_does_not_advertise_close(kind):
    definition = ComponentDefinition(
        "Reference Window", object_type=kind,
        actions=frozenset({"resolve", "close"}),
        strategies=(ComponentStrategy("reference_inspection", {"component_id": "reference"}),),
    )
    assert not definition.supports(ActionType.CLOSE)
    assert "close" not in [action.action_id for action in actions_for(definition)]


def test_close_failure_never_retries_another_native_backend(monkeypatch):
    """A close refusal is not permission to dispatch a second close request."""
    import automation_harness.core.component_handle as handle_module
    called = []

    class JavaFx:
        def __init__(self, _context):
            pass

        def close_window(self, **_kwargs):
            called.append("javafx")
            raise RuntimeError("JavaFX Stage remained visible (request may have been consumed)")

    class Atspi:
        def __init__(self, _context):
            pass

        def close_window(self, **_kwargs):
            called.append("atspi")
            return {"closed": True}

    monkeypatch.setattr(handle_module, "JavaFxBridgeDriver", JavaFx)
    monkeypatch.setattr(handle_module, "AtspiDriver", Atspi)
    definition = ComponentDefinition(
        "Advanced Details Window", object_type=ObjectType.WINDOW,
        actions=frozenset({"close"}),
        strategies=(
            ComponentStrategy("javafx", {"identification": {
                "assistive": {"window": "Advanced Details"},
            }}),
            ComponentStrategy("atspi", {"identification": {
                "mandatory": {"name": "Advanced Details"},
            }}),
        ),
    )
    context = SimpleNamespace(evidence=SimpleNamespace(record=lambda *a, **k: None))
    handle = ComponentHandle(context, definition)
    from automation_harness.core.component_handle import ComponentResolutionError

    with pytest.raises(ComponentResolutionError, match="no fallback close dispatched"):
        handle._accessibility_operation("close window", "close_window", fail_closed=True)
    assert called == ["javafx"]


def test_nonclose_accessibility_operations_retain_existing_fallback(monkeypatch):
    import automation_harness.core.component_handle as handle_module
    called = []

    class JavaFx:
        def __init__(self, _context):
            pass
        def get_value(self, **_kwargs):
            called.append("javafx")
            raise LookupError("control not found")

    class Atspi:
        def __init__(self, _context):
            pass
        def get_value(self, **_kwargs):
            called.append("atspi")
            return 12

    monkeypatch.setattr(handle_module, "JavaFxBridgeDriver", JavaFx)
    monkeypatch.setattr(handle_module, "AtspiDriver", Atspi)
    definition = ComponentDefinition(
        "Value", object_type=ObjectType.SLIDER,
        strategies=(
            ComponentStrategy("javafx", {"identification": {"mandatory": {"id": "value"}}}),
            ComponentStrategy("atspi", {"identification": {"mandatory": {"name": "Value"}}}),
        ),
    )
    context = SimpleNamespace(evidence=SimpleNamespace(record=lambda *a, **k: None))
    assert ComponentHandle(context, definition)._accessibility_operation(
        "read value", "get_value",
    ) == 12
    assert called == ["javafx", "atspi"]


def test_javafx_close_failure_provides_dispatcher_evidence_without_forced_hide():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    source = (
        root / "javafx_agent/src/main/java/com/automationharness/javafx/"
        "AutomationHarnessJavaFxAgent.java"
    ).read_text(encoding="utf-8")
    assert 'event_consumed=' in source
    assert 'event_dispatcher=' in source
    assert 'on_close_request_present=' in source
    assert 'no additional hide/close requested' in source


def test_legacy_window_proxy_caption_supplies_exact_stage_scope():
    """ERSA Advanced Details window proxy predates explicit ownership.window."""
    from automation_harness.core.component_handle import ComponentHandle

    definition = ComponentDefinition(
        "Advanced Details Window",
        object_type=ObjectType.WINDOW,
        framework="javafx",
        properties={"window_proxy": True, "semantic_container": True},
        strategies=(ComponentStrategy("javafx", {
            "identification": {
                "mandatory": {"accessible_text": "Advanced Details"},
            },
        }),),
    )
    handle = ComponentHandle(SimpleNamespace(), definition)
    assert handle._owning_javafx_window() == "Advanced Details"
    assert handle._scoped_identification(
        "javafx", definition.strategies[0].options["identification"],
    ) == {
        "mandatory": {"accessible_text": "Advanced Details"},
        "assistive": {"window": "Advanced Details"},
    }


def test_root_node_accessible_text_is_not_a_window_title_fallback():
    definition = ComponentDefinition(
        "Advanced Details Display",
        object_type=ObjectType.TAB_CONTAINER,
        properties={"window_proxy": True},
        strategies=(ComponentStrategy("javafx", {"identification": {
            "mandatory": {"accessible_text": "Advanced Details"},
        }}),),
    )
    handle = ComponentHandle(SimpleNamespace(), definition)
    assert handle._owning_javafx_window() is None
    assert handle._scoped_identification(
        "javafx", definition.strategies[0].options["identification"],
    ) == {"mandatory": {"accessible_text": "Advanced Details"}}


def test_window_proxy_requires_explicit_marker_before_using_caption():
    definition = ComponentDefinition(
        "Unverified Window",
        object_type=ObjectType.WINDOW,
        strategies=(ComponentStrategy("javafx", {"identification": {
            "mandatory": {"accessible_text": "Advanced Details"},
        }}),),
    )
    assert ComponentHandle(SimpleNamespace(), definition)._owning_javafx_window() is None


def test_legacy_window_proxy_conflicting_captions_fail_closed():
    from automation_harness.core.component_handle import ComponentResolutionError
    definition = ComponentDefinition(
        "Conflicting Window", object_type=ObjectType.WINDOW,
        properties={"window_proxy": True},
        strategies=(
            ComponentStrategy("javafx", {"identification": {
                "mandatory": {"accessible_text": "Advanced Details"},
            }}),
            ComponentStrategy("javafx", {"identification": {
                "mandatory": {"accessible_text": "Other Stage"},
            }}),
        ),
    )
    with pytest.raises(ComponentResolutionError, match="conflicting JavaFX Stage captions"):
        ComponentHandle(SimpleNamespace(), definition)._owning_javafx_window()
