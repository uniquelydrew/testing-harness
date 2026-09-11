from automation_harness.core.logical_menu import (
    durable_menu_criteria,
    find_logical_menu_targets,
    is_javafx_menu_skin_capture,
    menu_action_payload,
)
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy
from automation_harness.models.gui import ObjectType


def _live_camera_selector_capture():
    # Mirrors the live ERSA repository sample: the disposable CSS bridge node
    # has the durable logical MenuItem id but otherwise only skin metadata.
    return CapturedComponent(
        name="cameraSelectorMenuItem",
        role="menu item",
        description="cameraSelectorMenuItem",
        accessible_id="cameraSelectorMenuItem",
        application="ContextMenu",
        window="ContextMenu",
        hierarchy=("Pane", "CSSBridge", "ContextMenuContent", "MenuBox", "MenuItemContainer#cameraSelectorMenuItem"),
        actions=("activate", "resolve"),
        bounds=None,
        state=ComponentState(present=True, visible=True),
        backend_properties={"style_classes": ["menu-item"], "node_ref": "n274"},
        framework="javafx",
        native_class="com.sun.javafx.scene.control.ContextMenuContent$MenuItemContainer",
        object_type=ObjectType.CUSTOM,
    )


def _owner():
    return ComponentDefinition(
        component_id="Pane.CSSBridge.ContextMenu",
        description="CSS bridge context menu",
        framework="javafx",
        object_type=ObjectType.MENU,
        strategies=(ComponentStrategy("javafx", {"identification": {"mandatory": {"id": "cssBridge"}}}),),
        subobjects={
            "camera": {
                "kind": "menu",
                "selector": {"criteria": {"id": "cameraMenu", "text": "Camera"}, "ordinal": 0},
                "subobjects": {
                    "camera_selector": {
                        "kind": "menu_item",
                        "selector": {"criteria": {"id": "cameraSelectorMenuItem", "text": "Camera Selector"}, "ordinal": 0},
                    }
                },
            }
        },
    )


def test_live_context_menu_skin_is_not_durable_identity():
    capture = _live_camera_selector_capture()
    assert is_javafx_menu_skin_capture(capture)
    assert durable_menu_criteria(capture) == {"id": "cameraSelectorMenuItem"}


def test_live_context_menu_item_matches_nested_logical_subobject_by_id():
    matches = find_logical_menu_targets((_owner(),), _live_camera_selector_capture())
    assert len(matches) == 1
    match = matches[0]
    assert match.owner_component_id == "Pane.CSSBridge.ContextMenu"
    assert match.subobject_path == ("camera", "camera_selector")
    assert [selector["criteria"]["id"] for selector in match.selectors] == ["cameraMenu", "cameraSelectorMenuItem"]
    assert menu_action_payload(match) == {
        "type": "select_menu_item",
        "subobject_path": ["camera", "camera_selector"],
    }


def test_duplicate_live_skin_capture_still_maps_to_one_logical_item():
    # The supplied live data contains two separately recorded MenuItemContainer
    # objects with different object_ids/PIDs but the same logical id.  They must
    # converge on one repository subobject rather than create -2 duplicates.
    first = _live_camera_selector_capture()
    second = CapturedComponent(**{**first.__dict__, "backend_properties": {"node_ref": "n274", "bridge_pid": 4095}})
    assert find_logical_menu_targets((_owner(),), first) == find_logical_menu_targets((_owner(),), second)
