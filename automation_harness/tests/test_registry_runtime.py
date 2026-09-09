from types import SimpleNamespace

import pytest

from automation_harness.authoring.registry_runtime import _effective_repository, _expanded_plan
from automation_harness.authoring.step_registry import LoadedStepRegistryResources
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.models.plan import StepCall, TestPlan


def _component(component_id, object_id):
    return ComponentRepository.from_document({
        "version": 3,
        "components": {
            component_id: {
                "object_id": object_id,
                "object_type": "button",
                "actions": ["click"],
                "strategies": [
                    {"type": "atspi", "name": component_id, "role": "push button"}
                ],
            }
        },
    })


def test_effective_repository_combines_active_and_registry_objects():
    active = _component("plan.button", "00000000-0000-0000-0000-000000000001")
    registry = _component("registry.button", "00000000-0000-0000-0000-000000000002")
    app = SimpleNamespace(
        repository=active,
        registry_resources=LoadedStepRegistryResources((), {}, registry),
    )

    effective = _effective_repository(app)

    assert effective.contains("plan.button")
    assert effective.contains("registry.button")


def test_effective_repository_rejects_name_conflicts():
    active = _component("shared.button", "00000000-0000-0000-0000-000000000001")
    registry = _component("shared.button", "00000000-0000-0000-0000-000000000002")
    app = SimpleNamespace(
        repository=active,
        registry_resources=LoadedStepRegistryResources((), {}, registry),
    )

    with pytest.raises(ValueError, match="conflicts"):
        _effective_repository(app)


def test_expanded_plan_keeps_authored_registry_call_outside_runtime_projection():
    reusable = ReusableStepDefinition(
        step_id="navigation.open",
        name="Open",
        description="",
        plan=TestPlan(
            name="Open",
            steps=(StepCall("click", "gui.object.action", {"component_id": "registry.button"}),),
        ),
        inputs={},
        outputs={},
    )
    authored = TestPlan(
        name="Example",
        steps=(StepCall("open", "navigation.open"),),
    )
    app = SimpleNamespace(
        plan=authored,
        registry_step_definitions={"navigation.open": reusable},
    )

    expanded = _expanded_plan(app)

    assert app.plan.steps[0].step_id == "navigation.open"
    assert expanded.steps[0].step_id == "gui.object.action"
    assert expanded.steps[0].node_id == "open::click"
