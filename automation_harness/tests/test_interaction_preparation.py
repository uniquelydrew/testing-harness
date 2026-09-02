from automation_harness.core.interaction_preparation import (
    InteractionPreparation,
    PreparationOutcome,
    preparation_requirement,
    validate_preparation,
)
from automation_harness.models.gui import ActionType


def test_pointer_actions_raise_window_without_preemptive_component_focus():
    requirement = preparation_requirement(ActionType.CLICK)
    assert requirement.activate_window
    assert requirement.require_window_activation
    assert not requirement.request_focus
    assert not requirement.require_focus


def test_text_actions_require_verified_component_focus():
    requirement = preparation_requirement(ActionType.SET_TEXT)
    assert requirement.activate_window
    assert requirement.request_focus
    assert requirement.require_focus


def test_semantic_activation_attempts_focus_without_requiring_focusability():
    requirement = preparation_requirement(ActionType.ACTIVATE)
    assert requirement.activate_window
    assert requirement.request_focus
    assert not requirement.require_focus


def test_required_focus_failure_is_rejected():
    preparation = InteractionPreparation(
        "atspi",
        PreparationOutcome("activate_window", True, True),
        PreparationOutcome("focus", True, False, error="focus state did not change"),
    )
    try:
        validate_preparation(preparation_requirement(ActionType.SET_TEXT), preparation)
    except RuntimeError as exc:
        assert "focus state did not change" in str(exc)
    else:
        raise AssertionError("required focus failure was accepted")


def test_non_focusable_semantic_action_can_continue():
    preparation = InteractionPreparation(
        "atspi",
        PreparationOutcome("activate_window", True, True),
        PreparationOutcome("focus", True, False, supported=False, error="not focusable"),
    )
    validate_preparation(preparation_requirement(ActionType.ACTIVATE), preparation)


from types import SimpleNamespace

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.models.component import ComponentDefinition, ComponentStrategy, ResolvedComponent
from automation_harness.models.gui import ObjectType


class _Evidence:
    def __init__(self):
        self.events = []

    def record(self, event, **payload):
        self.events.append((event, payload))


def _live_handle(actions=frozenset({"focus", "set_text", "resolve"})):
    context = SimpleNamespace(backend="live-desktop", evidence=_Evidence())
    definition = ComponentDefinition(
        component_id="editor.name",
        object_type=ObjectType.TEXT_FIELD,
        actions=actions,
        strategies=(ComponentStrategy(
            "atspi",
            {"identification": {"mandatory": {"name": "Name"}}},
        ),),
    )
    return ComponentHandle(context, definition)


def test_explicit_focus_uses_preparation_without_semantic_activation(monkeypatch):
    handle = _live_handle()
    calls = []
    monkeypatch.setattr(
        "automation_harness.core.component_handle.AtspiDriver.activate_window",
        lambda self, **kwargs: calls.append("window") or {"window": "Editor"},
    )
    monkeypatch.setattr(
        "automation_harness.core.component_handle.AtspiDriver.focus",
        lambda self, **kwargs: calls.append("focus") or {"focused": True},
    )
    result = handle.execute(ActionType.FOCUS)
    assert calls == ["window", "focus"]
    assert result.action == ActionType.FOCUS
    assert result.result["focused"] is True


def test_text_action_stops_before_mutation_when_focus_cannot_be_verified(monkeypatch):
    handle = _live_handle()
    monkeypatch.setattr(
        "automation_harness.core.component_handle.AtspiDriver.activate_window",
        lambda self, **kwargs: {"window": "Editor"},
    )
    monkeypatch.setattr(
        "automation_harness.core.component_handle.AtspiDriver.focus",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("not focused")),
    )
    monkeypatch.setattr(handle, "set_text", lambda value: (_ for _ in ()).throw(AssertionError("mutated")))
    try:
        handle.execute({"type": "set_text", "value": "Drew"})
    except RuntimeError as exc:
        assert "not focused" in str(exc)
    else:
        raise AssertionError("text mutation continued after focus failure")


def test_pointer_action_activates_window_then_resolves_current_bounds(monkeypatch):
    handle = _live_handle(frozenset({"click", "resolve"}))
    order = []
    monkeypatch.setattr(
        "automation_harness.core.component_handle.AtspiDriver.activate_window",
        lambda self, **kwargs: order.append("window") or {"window": "Editor"},
    )
    monkeypatch.setattr(
        handle,
        "resolve",
        lambda: order.append("resolve") or ResolvedComponent(
            "editor.name", "atspi", {"bounds": [20, 30, 40, 10]},
        ),
    )
    monkeypatch.setattr(
        "automation_harness.core.component_handle.click_bounds",
        lambda bounds, action: order.append("click") or {"bounds": bounds},
    )
    handle.execute(ActionType.CLICK)
    assert order == ["window", "resolve", "click"]
