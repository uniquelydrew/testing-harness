from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_identity_sync import (
    find_existing_component_ids,
    rename_plan_component,
    rename_repository_component,
)
from automation_harness.models.component import (
    CapturedComponent,
    ComponentDefinition,
    ComponentState,
    ComponentStrategy,
)
from automation_harness.models.gui import ObjectType
from automation_harness.models.plan import StepCall, TestPlan


def _javafx_capture(component_id="cameraSelectorMenuItem"):
    return CapturedComponent(
        name="Camera Selector",
        role="menu item",
        description=None,
        accessible_id=component_id,
        application="ContextMenu",
        window="ContextMenu",
        hierarchy=("ContextMenuContent", "MenuItemContainer#%s" % component_id),
        actions=("activate",),
        bounds=(100, 100, 120, 24),
        state=ComponentState(present=True, visible=True),
        framework="javafx",
        native_class="javafx.scene.control.MenuItem",
        object_type=ObjectType.MENU_ITEM,
        authored_strategy=ComponentStrategy("javafx", {
            "identification": {
                "mandatory": {"id": component_id},
                "assistive": {
                    "accessible_role": "MENU_ITEM",
                    "window": "ContextMenu",
                },
            }
        }),
    )


def test_custom_component_name_does_not_change_capture_matching():
    definition = ComponentDefinition(
        component_id="Camera Selector Command",
        object_id="11111111-1111-1111-1111-111111111111",
        object_type=ObjectType.MENU_ITEM,
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "cameraSelectorMenuItem"}}
        }),),
    )
    repository = ComponentRepository({definition.component_id: definition})

    assert find_existing_component_ids(repository, _javafx_capture()) == (
        "Camera Selector Command",
    )


def test_capture_identity_conflict_does_not_match_existing_component():
    definition = ComponentDefinition(
        component_id="Camera Selector Command",
        object_type=ObjectType.MENU_ITEM,
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "anotherMenuItem"}}
        }),),
    )
    repository = ComponentRepository({definition.component_id: definition})

    assert find_existing_component_ids(repository, _javafx_capture()) == ()


def test_repository_rename_preserves_immutable_object_id_and_removes_old_name():
    definition = ComponentDefinition(
        component_id="Generated.Name",
        object_id="22222222-2222-2222-2222-222222222222",
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "saveButton"}}
        }),),
    )
    repository = ComponentRepository({definition.component_id: definition})

    renamed = rename_repository_component(repository, "Generated.Name", "Save")

    assert "Generated.Name" not in renamed.components
    assert renamed.get("Save").object_id == definition.object_id
    assert renamed.get(definition.object_id).component_id == "Save"


def test_plan_component_rename_updates_steps_inline_objects_and_definitions():
    plan = TestPlan(
        name="rename",
        steps=(StepCall(
            node_id="step-1",
            step_id="gui.object.action",
            inputs={"component_id": "Generated.Name", "action": {"type": "click"}},
            scope={"owner_component_id": "Generated.Name"},
        ),),
        objects={"Generated.Name": {"object_id": "object-1"}},
        step_definitions={
            "custom.step": {
                "inputs": {"component_id": "Generated.Name"},
                "description": "Generated.Name is text here and should not be rewritten",
            }
        },
    )

    renamed = rename_plan_component(plan, "Generated.Name", "Save")

    assert renamed.steps[0].inputs["component_id"] == "Save"
    assert renamed.steps[0].scope["owner_component_id"] == "Save"
    assert "Generated.Name" not in renamed.objects
    assert renamed.objects["Save"]["object_id"] == "object-1"
    assert renamed.step_definitions["custom.step"]["inputs"]["component_id"] == "Save"
    assert renamed.step_definitions["custom.step"]["description"] == (
        "Generated.Name is text here and should not be rewritten"
    )
