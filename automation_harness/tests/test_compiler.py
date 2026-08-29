from __future__ import annotations

from automation_harness.core.compiler import COMPILED_TEST_FORMAT, compile_test
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import default_step_registry
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan


def _components() -> ComponentRepository:
    return ComponentRepository.from_document({
        "version": 2,
        "components": {
            "dialog.ok": {
                "object_type": "button",
                "actions": ["activate"],
                "strategies": [{
                    "type": "atspi",
                    "identification": {"mandatory": {"name": "OK", "role": "push button"}},
                }],
                "scope": {"parent": {"from": "execution.active_window"}},
                "assertions": {
                    "ready": {"all": [{"state": "visible", "equals": True}, {"state": "enabled", "equals": True}]}
                },
            }
        },
    })


def test_compiler_embeds_dependencies_completion_scope_and_variable_lineage():
    plan = TestPlan(
        name="compiled-flow",
        variables={"target": "dialog.ok"},
        steps=(
            StepCall(
                node_id="activate",
                step_id="navigation.component.resolve",
                inputs={"component_id": "dialog.ok"},
                outputs={"component_id": "activated_component"},
                scope={"parent": {"from": "execution.active_window"}},
                completion={
                    "mode": "explicit",
                    "condition": {"object": "dialog.ok", "state": "absent"},
                },
            ),
            StepCall(
                node_id="verify",
                step_id="validation.equal",
                inputs={"name": "activated", "actual": PlanVariableRef("activated_component"), "expected": "dialog.ok"},
            ),
        ),
    )
    compiled = compile_test(plan, default_step_registry(), _components())
    payload = compiled.to_dict()

    assert payload["format"] == COMPILED_TEST_FORMAT
    assert payload["dependencies"]["components"]["dialog.ok"]["scope"]["parent"]["from"] == "execution.active_window"
    assert "ready" in payload["dependencies"]["components"]["dialog.ok"]["assertions"]
    assert payload["instructions"][0]["completion"]["mode"] == "explicit"
    assert payload["variables"]["producers"]["activated_component"]["invocation_id"] == "activate"
    assert payload["variables"]["consumers"]["activated_component"][0]["invocation_id"] == "verify"
    assert len(compiled.digest) == 64


def test_compilation_is_deterministic():
    plan = TestPlan(
        name="deterministic",
        steps=(StepCall(node_id="check", step_id="validation.equal", inputs={"name": "x", "actual": 1, "expected": 1}),),
    )
    first = compile_test(plan, default_step_registry(), ComponentRepository({}))
    second = compile_test(plan, default_step_registry(), ComponentRepository({}))
    assert first.to_json() == second.to_json()
