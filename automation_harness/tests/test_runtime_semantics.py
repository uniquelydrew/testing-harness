from __future__ import annotations

from automation_harness.core.completion import _effects_condition, await_step_completion
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.execution_context import ExecutionContextStack, bind_component_lineage
from automation_harness.core.predicates import compare, evaluate_state
from automation_harness.models.component import ComponentState
from automation_harness.models.plan import PlanVariableRef, StepCall


def _repository() -> ComponentRepository:
    return ComponentRepository.from_document({
        "version": 2,
        "components": {
            "dialog.confirm": {
                "object_type": "dialog",
                "actions": ["close"],
                "strategies": [{
                    "type": "atspi",
                    "identification": {"mandatory": {"name": "Confirm", "role": "dialog"}},
                }],
            },
            "common.ok": {
                "object_type": "button",
                "actions": ["activate"],
                "strategies": [{
                    "type": "atspi",
                    "identification": {"mandatory": {"name": "OK", "role": "push button"}},
                }],
                "scope": {"parent": {"from": "execution.active_window"}},
                "action_completion": {
                    "click": {"effects": [{"object": "dialog.confirm", "transition": "becomes-absent"}]}
                },
            },
        },
    })


def test_predicates_support_compound_state_expressions():
    state = ComponentState(present=True, visible=True, enabled=True, properties={"text": "Ready"})
    assert evaluate_state(state, {"all": [
        {"state": "visible", "equals": True},
        {"property": "text", "operator": "matches", "expected": "^Re"},
    ]})
    assert compare("automation harness", "contains", "harness")


def test_repository_round_trips_completion_and_scope_contracts():
    repository = _repository()
    definition = repository.get("common.ok")
    document = repository.to_document()["components"]["common.ok"]

    assert definition.scope == {"parent": {"from": "execution.active_window"}}
    assert "click" in definition.action_completion
    assert document["scope"] == definition.scope
    assert document["action_completion"] == definition.action_completion


def test_lineage_binding_uses_component_default_scope_without_mutating_repository():
    repository = _repository()
    original = repository.get("common.ok")
    context = ExecutionContextStack()
    context.push_window("dialog.confirm")
    bound = bind_component_lineage(
        original,
        context.effective_scope(original),
        repository,
        active_window=context.active_window,
    )

    assert "parent" not in original.strategies[0].options["identification"].get("assistive", {})
    assert bound.strategies[0].options["identification"]["assistive"]["parent"] == {
        "name": "Confirm",
        "role": "dialog",
    }


def test_dispatch_only_completion_does_not_observe_objects():
    class Evidence:
        def __init__(self):
            self.events = []

        def record(self, event, **fields):
            self.events.append((event, fields))

    class Context:
        evidence = Evidence()

    call = StepCall(
        node_id="fire-and-forget",
        step_id="validation.equal",
        completion={"mode": "dispatch-only"},
    )
    await_step_completion(Context(), call, {})
    assert Context.evidence.events[0][0] == "step_completion_skipped"


def test_explicit_completion_resolves_plan_variable_references():
    class Evidence:
        def __init__(self):
            self.events = []

        def record(self, event, **fields):
            self.events.append((event, fields))

    class Globals:
        values = {"actual": "ready", "expected": "ready"}

        def get(self, path):
            return self.values[path]

    class Context:
        evidence = Evidence()
        globals = Globals()

    call = StepCall(
        node_id="wait-on-global",
        step_id="validation.equal",
        completion={
            "mode": "explicit",
            "condition": {
                "variable": "actual",
                "equals": PlanVariableRef("expected"),
            },
        },
    )

    await_step_completion(Context(), call, {})

    assert Context.evidence.events[-1][0] == "step_completion_satisfied"
    assert Context.evidence.events[-1][1]["condition"]["equals"] == "ready"


def test_completion_effects_compile_to_all_affected_object_predicates():
    assert _effects_condition([
        {"object": "checkout.processing", "transition": "becomes-visible"},
        {"object": "checkout.dialog", "transition": "becomes-absent"},
    ]) == {
        "all": [
            {"object": "checkout.processing", "state": "visible", "equals": True},
            {"object": "checkout.dialog", "state": "absent", "equals": True},
        ]
    }


def test_context_effects_restore_parent_window_for_nested_dialogs():
    context = ExecutionContextStack()
    context.apply_effect({"operation": "push", "object": "settings.dialog"})
    context.apply_effect({"operation": "push", "object": "settings.confirm"})
    assert context.active_window == "settings.confirm"
    assert context.apply_effect({"operation": "pop", "expected": "settings.confirm"}) == "settings.confirm"
    assert context.active_window == "settings.dialog"
