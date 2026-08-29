from __future__ import annotations

from pathlib import Path

from automation_harness.core.completion import await_step_completion
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.execution_context import bind_component_lineage
from automation_harness.core.execution_context import ExecutionSignals
from automation_harness.core.predicates import compare, evaluate_state
from automation_harness.models.component import ComponentState
from automation_harness.models.plan import StepCall


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
                "assertions": {
                    "ready": {"all": [
                        {"state": "visible", "equals": True},
                        {"state": "enabled", "equals": True},
                    ]},
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


def test_lineage_binding_creates_contextual_definition_without_mutating_repository():
    repository = _repository()
    original = repository.get("common.ok")
    bound = bind_component_lineage(
        original,
        original.scope,
        repository,
        active_window="dialog.confirm",
    )
    assert "parent" not in original.strategies[0].options["identification"].get("assistive", {})
    assert bound.strategies[0].options["identification"]["assistive"]["parent"] == {
        "name": "Confirm",
        "role": "dialog",
    }


def test_dispatch_only_completion_does_not_observe_objects(tmp_path: Path):
    class Evidence:
        def __init__(self): self.events = []
        def record(self, event, **fields): self.events.append((event, fields))

    class Context:
        evidence = Evidence()

    call = StepCall(
        node_id="fire-and-forget",
        step_id="validation.equal",
        completion={"mode": "dispatch-only"},
    )
    await_step_completion(Context(), call, {})
    assert Context.evidence.events[0][0] == "step_completion_skipped"


def test_execution_signals_use_generation_baselines():
    signals = ExecutionSignals()
    before = signals.snapshot()
    signals.signal("export-complete")
    assert signals.occurred_since("export-complete", before)
    after = signals.snapshot()
    assert not signals.occurred_since("export-complete", after)
