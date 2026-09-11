from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.core.reusable_step_snapshot import (
    load_snapshotted_reusable_steps,
    snapshot_reusable_dependencies,
)
from automation_harness.models.plan import StepCall, TestPlan


def _repository():
    return ComponentRepository.from_document({
        "version": 2,
        "components": {
            "login.submit": {
                "object_type": "button",
                "actions": ["click"],
                "strategies": [{"type": "atspi", "name": "Submit", "role": "push button"}],
            }
        },
    })


def test_snapshot_embeds_transitively_used_reusable_steps_and_objects():
    click = ReusableStepDefinition(
        step_id="common.submit",
        name="Submit",
        description="",
        plan=TestPlan(
            name="Submit",
            steps=(StepCall("click", "gui.object.action", {"component_id": "login.submit"}),),
        ),
        inputs={},
        outputs={},
    )
    login = ReusableStepDefinition(
        step_id="authentication.login",
        name="Login",
        description="",
        plan=TestPlan(
            name="Login",
            steps=(StepCall("submit", "common.submit"),),
        ),
        inputs={},
        outputs={},
    )
    plan = TestPlan(
        name="Scenario",
        steps=(StepCall("login", "authentication.login"),),
    )

    snapshotted = snapshot_reusable_dependencies(
        plan,
        {
            "authentication.login": login,
            "common.submit": click,
        },
        _repository(),
    )

    assert set(snapshotted.step_definitions) == {
        "authentication.login",
        "common.submit",
    }
    assert "login.submit" in snapshotted.objects
    loaded = load_snapshotted_reusable_steps(snapshotted)
    assert loaded["authentication.login"].plan.steps[0].step_id == "common.submit"
    assert loaded["common.submit"].plan.steps[0].inputs["component_id"] == "login.submit"


def test_snapshot_preserves_unrelated_step_definition_metadata():
    reusable = ReusableStepDefinition(
        step_id="common.noop",
        name="Noop",
        description="",
        plan=TestPlan(name="Noop", steps=(StepCall("noop", "validation.equals"),)),
        inputs={},
        outputs={},
    )
    plan = TestPlan(
        name="Scenario",
        step_definitions={"legacy.metadata": {"source": "existing"}},
        steps=(StepCall("noop", "common.noop"),),
    )

    snapshotted = snapshot_reusable_dependencies(
        plan,
        {"common.noop": reusable},
        ComponentRepository({}),
    )

    assert snapshotted.step_definitions["legacy.metadata"] == {"source": "existing"}
    assert snapshotted.step_definitions["common.noop"]["kind"] == "reusable_step"
