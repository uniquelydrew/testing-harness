from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_identity_sync import (
    find_existing_component_ids,
    readable_plan_component_references,
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


def test_same_named_objects_under_different_parents_do_not_collapse():
    definition = ComponentDefinition(
        component_id="Primary.Save",
        object_type=ObjectType.BUTTON,
        strategies=(ComponentStrategy("atspi", {
            "identification": {
                "mandatory": {"name": "Save", "role": "push button"},
                "assistive": {"parent": {"accessible_id": "primary-pane"}},
            }
        }),),
    )
    repository = ComponentRepository({definition.component_id: definition})
    capture = CapturedComponent(
        name="Save", role="push button", description=None, accessible_id=None,
        application="Demo", window="Demo", hierarchy=("secondary-pane", "Save"),
        actions=("click",), bounds=(0, 0, 10, 10),
        state=ComponentState(present=True), parent_accessible_id="secondary-pane",
        object_type=ObjectType.BUTTON,
    )

    assert find_existing_component_ids(repository, capture) == ()


def test_missing_stored_identity_condition_is_not_treated_as_a_match():
    definition = ComponentDefinition(
        component_id="Stable.Save",
        object_type=ObjectType.BUTTON,
        strategies=(ComponentStrategy("atspi", {
            "identification": {
                "mandatory": {"accessible_id": "save-button", "role": "push button"},
                "assistive": {"name": "Save"},
            }
        }),),
    )
    repository = ComponentRepository({definition.component_id: definition})
    capture = CapturedComponent(
        name="Save", role="push button", description=None, accessible_id=None,
        application="Demo", window="Demo", hierarchy=("Save",),
        actions=("click",), bounds=(0, 0, 10, 10),
        state=ComponentState(present=True), object_type=ObjectType.BUTTON,
    )

    assert find_existing_component_ids(repository, capture) == ()


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


def test_plan_uuid_references_are_migrated_to_readable_component_names():
    definition = ComponentDefinition(
        component_id="File Menu",
        object_id="33333333-3333-3333-3333-333333333333",
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "fileMenu"}}
        }),),
    )
    repository = ComponentRepository({definition.component_id: definition})
    plan = TestPlan(
        name="readable",
        steps=(StepCall(
            node_id="step-1",
            step_id="gui.object.action",
            inputs={"component_id": definition.object_id, "action": {"type": "click"}},
            completion={"object": definition.object_id, "state": "visible", "equals": True},
        ),),
    )

    migrated = readable_plan_component_references(plan, repository)

    assert migrated.steps[0].inputs["component_id"] == "File Menu"
    assert migrated.steps[0].completion["object"] == "File Menu"
    assert repository.get("File Menu").object_id == definition.object_id


def test_solipsys_track_identity_matches_across_runtime_ref_and_position_changes():
    identification = {
        "mandatory": {
            "rendered_class": "com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
            "track_class": "com.solipsys.msct.track.report.MSCTTrackReport",
            "track_identity_key": "field:identity",
            "track_identity_value": "2",
        },
        "assistive": {
            "accessible_id": "panel0",
            "native_class": "com.solipsys.view.AWTViewCanvas",
            "window": "MSCT Domain 12",
        },
    }
    definition = ComponentDefinition(
        component_id="Track 2", object_type=ObjectType.CUSTOM,
        framework="solipsys_rendered",
        native_class="com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
        strategies=(ComponentStrategy("java_agent", {"identification": identification}),),
    )
    repository = ComponentRepository({definition.component_id: definition})
    capture = CapturedComponent(
        name="2", role="rendered_object", description=None, accessible_id=None,
        application="MSCT Domain 12", window="MSCT Domain 12",
        hierarchy=("AWTViewCanvas", "DefaultTrackVelocityDisplay2D"),
        actions=("resolve", "click"), bounds=(1297, 227, 1, 1),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={"ref": "different-runtime-display-object"},
        authored_strategy=ComponentStrategy("java_agent", {"identification": identification}),
        object_type=ObjectType.CUSTOM, framework="solipsys_rendered",
        native_class="com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
    )

    assert find_existing_component_ids(repository, capture) == ("Track 2",)
