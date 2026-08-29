from __future__ import annotations

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_plan import validate_plan_components
from automation_harness.models.plan import StepCall, TestPlan


def _repository():
    return ComponentRepository.from_document({"version": 2, "components": {
        "login.submit": {
            "object_type": "button",
            "actions": ["click"],
            "strategies": [{"type": "atspi", "name": "Submit", "role": "push button"}],
        },
    }})


def test_test_plan_reference_survives_object_rename():
    repository = _repository()
    object_id = repository.get("login.submit").object_id
    plan = TestPlan(
        name="stable-object-reference",
        steps=(
            StepCall(
                node_id="click",
                step_id="gui.button.click",
                inputs={"component_id": object_id},
            ),
        ),
    )

    renamed = repository.rename("login.submit", "login.continue")

    assert validate_plan_components(plan, renamed) == []
    assert renamed.get(object_id).component_id == "login.continue"


def test_gui_object_identity_uses_hidden_immutable_id_and_keeps_display_name():
    repository = _repository().rename("login.submit", "login.continue")
    definition = repository.get("login.continue")
    handle = ComponentHandle(object(), definition)

    assert handle.identity.repository_id == definition.object_id
    assert handle.identity.name == "login.continue"
