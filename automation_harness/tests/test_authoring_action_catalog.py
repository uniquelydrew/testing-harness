from automation_harness.authoring.action_catalog import action_by_id, actions_for
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import validate_plan, validate_plan_components
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.models.plan import TestPlan


def _component(kind, actions):
    return ComponentDefinition(
        "screen.object", object_type=kind, actions=frozenset(actions),
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"name": "Object"}}}),),
    )


def test_button_actions_are_contextual_and_include_observation_options():
    definition = _component(ObjectType.BUTTON, {"click", "focus"})
    action_ids = [item.action_id for item in actions_for(definition)]
    assert "click" in action_ids
    assert "set_text" not in action_ids
    assert action_ids[-3:] == ["wait_for_state", "assert_state", "read_property"]


def test_click_is_offered_for_general_interactive_components():
    definition = _component(ObjectType.PANEL, {"focus"})
    assert "click" in [item.action_id for item in actions_for(definition)]


def test_action_call_binds_selected_object_without_manual_input():
    definition = _component(ObjectType.BUTTON, {"click"})
    call = action_by_id(definition, "click").to_step_call("step-001", definition.component_id)
    assert call.step_id == "gui.object.action"
    assert call.inputs == {"component_id": "screen.object", "action": {"type": "click"}}


def test_typed_action_values_are_embedded_in_semantic_action():
    definition = _component(ObjectType.TEXT_FIELD, {ActionType.SET_TEXT.value})
    call = action_by_id(definition, "set_text").to_step_call("step-001", definition.component_id, {"value": "Drew"})
    assert call.inputs["action"] == {"type": "set_text", "value": "Drew"}


def test_assertion_uses_internal_executor_but_is_not_presented_as_step_library():
    definition = _component(ObjectType.BUTTON, {"click"})
    call = action_by_id(definition, "assert_state").to_step_call(
        "step-002", definition.component_id, {"state_name": "visible", "expected": True},
    )
    assert call.step_id == "gui.object.state.assert"
    assert call.inputs["component_id"] == "screen.object"


def test_capture_to_action_to_test_vertical_slice_validates():
    definition = _component(ObjectType.BUTTON, {"click"})
    repository = ComponentRepository({definition.component_id: definition})
    click = action_by_id(definition, "click").to_step_call("click-object", definition.component_id)
    assertion = action_by_id(definition, "assert_state").to_step_call(
        "assert-visible", definition.component_id, {"state_name": "visible", "expected": True},
    )
    plan = TestPlan("captured-object-test", steps=(click, assertion))
    assert validate_plan(plan, default_step_registry()) == []
    assert validate_plan_components(plan, repository) == []
