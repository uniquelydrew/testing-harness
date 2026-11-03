"""Regression coverage for generated, readable step names."""
from dataclasses import replace

from automation_harness.authoring.action_catalog import INTERACTIONS
from automation_harness.authoring.step_naming import (
    default_name_for_call,
    default_step_name,
    display_name_for_call,
    humanize_action,
    humanize_identifier,
)
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import ComponentDefinition
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.models.plan import StepCall


def test_identifier_normalization_preserves_acronyms_and_strips_owner_path():
    assert humanize_identifier("MSCTDisplay.WatchButton", leaf=True) == "Watch Button"
    assert humanize_identifier("Main_Window.watch_btn", leaf=True) == "Watch Button"
    assert humanize_identifier("HTTPServerURL") == "HTTP Server URL"
    assert humanize_identifier("MSCT Display") == "MSCT Display"
    assert humanize_identifier("camera_status_tab") == "Camera Status Tab"
    assert humanize_identifier("22222222-2222-4222-8222-222222222222") == ""


def test_names_use_action_plus_humanized_object_and_type():
    assert default_step_name("click", "MSCT Display.WatchButton", ObjectType.BUTTON) == "Click Watch Button"
    assert default_step_name("click", "Watch", ObjectType.BUTTON) == "Click Watch Button"
    assert default_step_name("close", "SelectSiteWindow", ObjectType.WINDOW) == "Close Select Site Window"
    assert default_step_name("select_item", "site_choice_box") == "Select Item Site Choice Box"
    assert default_step_name("select_menu_item", "MenuBar.FileMenu", ObjectType.MENU) == "Select Menu Option File Menu"
    assert default_step_name("gui.object.visual.assert", "WatchButton") == "Assert Match Watch Button"
    assert default_step_name("click", "Watch Button", ObjectType.BUTTON) == "Click Watch Button"
    assert default_step_name("click", "WatchButton", ObjectType.TOGGLE_BUTTON) == "Click Watch Button"
    assert default_step_name("Wait for State", "loginDialog", ObjectType.DIALOG) == "Wait for State Login Dialog"


def test_action_definition_uses_human_label_while_preserving_canonical_uuid():
    object_id = "11111111-1111-4111-8111-111111111111"
    call = INTERACTIONS[ActionType.CLICK].to_step_call(
        "action-1", object_id, {},
        object_label="MSCTDisplay.watchButton", object_type=ObjectType.BUTTON,
    )
    assert call.name == "Click Watch Button"
    assert call.inputs["component_id"] == object_id
    assert call.inputs["action"]["type"] == "click"


def test_readable_call_name_resolves_repository_uuid_and_preserves_manual_rename():
    item = ComponentDefinition(
        "MSCT Display.WatchButton", object_type=ObjectType.BUTTON,
        object_id="11111111-1111-4111-8111-111111111111",
    )
    repository = ComponentRepository({item.component_id: item})
    call = StepCall(
        node_id="n1", step_id="gui.object.action",
        inputs={"component_id": item.object_id, "action": {"type": "click"}},
    )
    assert default_name_for_call(call, repository) == "Click Watch Button"
    assert display_name_for_call(call, repository) == "Click Watch Button"
    assert default_name_for_call(call, None) == "Click Object"
    assert display_name_for_call(replace(call, name="Verify watch is available"), repository) == (
        "Verify watch is available"
    )


def test_non_object_steps_keep_friendly_fallback_without_mutating_step_id():
    call = StepCall(node_id="n2", step_id="component.state.wait")
    assert default_name_for_call(call) == "Wait"
    assert call.name == ""
    assert call.step_id == "component.state.wait"
