from automation_harness.core.reusable_steps import ReusableStepDefinition, list_reusable_steps
from automation_harness.models.plan import StepCall, TestPlan


def test_reusable_steps_exist_only_after_user_saves_composition(tmp_path):
    directory = tmp_path / "reusable_steps"
    assert list_reusable_steps(directory) == ()
    plan = TestPlan("login", steps=(
        StepCall("step-001", "gui.object.action", {"component_id": "login.submit", "action": {"type": "click"}}),
    ))
    saved = ReusableStepDefinition("authentication.login", "Log in", "", plan, {}, {}).save(directory)
    assert saved.is_file()
    loaded = list_reusable_steps(directory)
    assert len(loaded) == 1
    assert loaded[0].step_id == "authentication.login"
    assert loaded[0].plan.steps[0].inputs["component_id"] == "login.submit"
