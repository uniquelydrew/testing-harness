from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.logical_menu import (
    durable_menu_criteria,
    find_logical_menu_targets,
    is_javafx_menu_skin_capture,
    menu_action_payload,
    normalize_menu_subobjects,
)
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.recording.session import RecordedInteraction, RecordingSession, interactions_to_steps


def _live_camera_selector_capture(native_class="com.sun.javafx.scene.control.ContextMenuContent$MenuItemContainer"):
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
        native_class=native_class,
        object_type=ObjectType.MENU_ITEM,
    )


def _owner():
    return ComponentDefinition(
        component_id="Pane.CSSBridge.ContextMenu",
        description="CSS bridge context menu",
        framework="javafx",
        object_type=ObjectType.MENU,
        actions=frozenset({"resolve", "select_menu_item"}),
        strategies=(ComponentStrategy("javafx", {"identification": {"mandatory": {"id": "cssBridge"}}}),),
        subobjects={
            "camera": {
                "kind": "menu",
                # Retain the legacy wrapper here deliberately; matching must
                # migrate it to the canonical runtime selector shape.
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


def test_logical_menu_item_is_recognized_after_skin_promotion():
    capture = _live_camera_selector_capture("javafx.scene.control.MenuItem")
    assert is_javafx_menu_skin_capture(capture)
    assert durable_menu_criteria(capture) == {"id": "cameraSelectorMenuItem"}


def test_live_context_menu_item_matches_nested_logical_subobject_by_id():
    owner = _owner()
    matches = find_logical_menu_targets((owner,), _live_camera_selector_capture())
    assert len(matches) == 1
    match = matches[0]
    assert match.owner_component_id == "Pane.CSSBridge.ContextMenu"
    assert match.subobject_path == ("camera", "camera_selector")
    assert [selector["criteria"]["id"] for selector in match.selectors] == ["cameraMenu", "cameraSelectorMenuItem"]
    assert menu_action_payload(match) == {
        "type": "select_menu_item",
        "path": ["camera", "camera_selector"],
    }
    # Matching also upgrades the in-memory definition so ComponentHandle's
    # existing menu-path executor can consume it without a second migration.
    assert owner.subobjects["camera"]["criteria"]["id"] == "cameraMenu"
    assert "selector" not in owner.subobjects["camera"]


def test_normalize_menu_subobjects_accepts_legacy_and_runtime_shapes():
    owner = _owner()
    normalized = normalize_menu_subobjects(owner.subobjects)
    assert normalized["camera"]["criteria"]["id"] == "cameraMenu"
    assert normalized["camera"]["ordinal"] == 0
    assert normalized["camera"]["subobjects"]["camera_selector"]["criteria"]["id"] == "cameraSelectorMenuItem"


def test_duplicate_live_skin_capture_still_maps_to_one_logical_item():
    # The supplied live data contains two separately recorded MenuItemContainer
    # objects with different object_ids/PIDs but the same logical id. They must
    # converge on one repository subobject rather than create -2 duplicates.
    owner = _owner()
    first = _live_camera_selector_capture()
    second = CapturedComponent(**{**first.__dict__, "backend_properties": {"node_ref": "n274", "bridge_pid": 4095}})
    assert find_logical_menu_targets((owner,), first) == find_logical_menu_targets((owner,), second)


def test_recording_matches_menu_item_to_owner_subobject_instead_of_top_level_object():
    owner = _owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    match = session._match(_live_camera_selector_capture("javafx.scene.control.MenuItem"))
    assert match.status == "known_subobject"
    assert match.component_id == owner.component_id
    assert match.subobject_path == ("camera", "camera_selector")


def test_recorded_menu_subobject_becomes_select_menu_item_action():
    owner = _owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    capture = _live_camera_selector_capture("javafx.scene.control.MenuItem")
    match = session._match(capture)
    interaction = RecordedInteraction(
        ActionType.CLICK,
        capture,
        {},
        1.0,
        1.0,
        repository_match=match,
    )
    step = interactions_to_steps((interaction,))[0]
    assert step.inputs == {
        "component_id": owner.component_id,
        "action": {
            "type": "select_menu_item",
            "path": ["camera", "camera_selector"],
        },
    }
