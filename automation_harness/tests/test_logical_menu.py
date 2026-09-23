import pytest

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.logical_menu import (
    durable_menu_criteria,
    find_logical_menu_targets,
    is_javafx_menu_skin_capture,
    menu_action_payload,
    normalize_menu_subobjects,
    resolve_authored_menu_route,
)
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.recording.observations import ActionFired, PointerInteraction
from automation_harness.recording.session import RecordedInteraction, RecordingSession, interactions_to_steps


def _live_camera_selector_capture(native_class="com.sun.javafx.scene.control.ContextMenuContent$MenuItemContainer"):
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


def _logical_camera_selector_capture():
    physical = _live_camera_selector_capture("javafx.scene.control.MenuItem")
    return CapturedComponent(**{
        **physical.__dict__,
        "name": "Camera Selector",
        "description": None,
        "hierarchy": (),
        "backend_properties": {
            "logical_menu": {
                "path": [
                    {"kind": "menu", "criteria": {"id": "cameraMenu", "text": "Camera"}},
                    {"kind": "menu item", "criteria": {"id": "cameraSelectorMenuItem", "text": "Camera Selector"}},
                ],
                "owner": {"kind": "context menu", "popup": {"role": "context menu"}},
            }
        },
    })


def _owner():
    return ComponentDefinition(
        component_id="Pane.CSSBridge.ContextMenu",
        description="CSS bridge context menu",
        framework="javafx",
        object_type=ObjectType.CONTEXT_MENU,
        actions=frozenset({"resolve", "select_menu_item"}),
        properties={"logical_owner": "context_menu"},
        strategies=(ComponentStrategy("javafx", {"identification": {"mandatory": {"id": "cssBridge"}}}),),
        subobjects={
            "camera": {
                "kind": "menu",
                "criteria": {"id": "cameraMenu", "text": "Camera"},
                "ordinal": 0,
                "subobjects": {
                    "camera_selector": {
                        "kind": "menu_item",
                        "criteria": {"id": "cameraSelectorMenuItem", "text": "Camera Selector"},
                        "ordinal": 0,
                    }
                },
            }
        },
    )


def _file_owner():
    return ComponentDefinition(
        component_id="AnchorPaneAnchorPane.MenuBar.HBox.MenuBarButtonFileMenu",
        description="File",
        framework="javafx",
        object_type=ObjectType.MENU,
        actions=frozenset({"resolve", "activate", "select_menu_item"}),
        strategies=(ComponentStrategy("javafx", {
            "identification": {
                "mandatory": {"id": "fileMenu"},
                "assistive": {"text": "File"},
            }
        }),),
        subobjects={},
    )


def _new_file_menu_item_capture():
    base = _live_camera_selector_capture("javafx.scene.control.MenuItem")
    return CapturedComponent(**{
        **base.__dict__,
        "name": "Open Recording",
        "accessible_id": "openRecordingMenuItem",
        "backend_properties": {
            "logical_menu": {
                "path": [
                    {
                        "kind": "menu",
                        "criteria": {"id": "fileMenu", "text": "File"},
                        "ordinal": 1,
                        "relative_offset": {"x": 15.0, "y": 5.0, "tolerance": 8.0},
                    },
                    {
                        "kind": "menu item",
                        "criteria": {"id": "openRecordingMenuItem", "text": "Open Recording"},
                        "ordinal": 2,
                        "relative_offset": {"x": 15.0, "y": 35.0, "tolerance": 8.0},
                    },
                ],
                "owner": {
                    "kind": "menu",
                    "logical": {"kind": "menu", "criteria": {"id": "fileMenu", "text": "File"}},
                },
            }
        },
    })


def test_live_context_menu_skin_is_not_durable_identity():
    capture = _live_camera_selector_capture()
    assert is_javafx_menu_skin_capture(capture)
    assert durable_menu_criteria(capture) == {"id": "cameraSelectorMenuItem"}


def test_logical_menu_item_is_recognized_after_skin_promotion():
    capture = _live_camera_selector_capture("javafx.scene.control.MenuItem")
    assert is_javafx_menu_skin_capture(capture)
    assert durable_menu_criteria(capture) == {"id": "cameraSelectorMenuItem"}


def test_skin_without_logical_route_cannot_guess_subobject_by_id():
    owner = _owner()
    assert find_logical_menu_targets((owner,), _live_camera_selector_capture()) == ()
    matches = find_logical_menu_targets((owner,), _logical_camera_selector_capture())
    assert len(matches) == 1
    match = matches[0]
    assert match.owner_component_id == "Pane.CSSBridge.ContextMenu"
    assert match.subobject_path == ("camera", "camera_selector")
    assert [selector["criteria"]["id"] for selector in match.selectors] == ["cameraMenu", "cameraSelectorMenuItem"]
    assert menu_action_payload(match) == {
        "type": "select_menu_item",
        "path": ["camera", "camera_selector"],
    }
    assert owner.subobjects["camera"]["criteria"]["id"] == "cameraMenu"
    assert "selector" not in owner.subobjects["camera"]


def test_identical_terminal_id_is_scoped_to_owner_and_full_route():
    from dataclasses import replace
    first = _owner()
    second = replace(_owner(), component_id="OtherContextMenu", object_id="other-owner",
                     strategies=(ComponentStrategy("javafx", {
                         "identification": {"mandatory": {"id": "otherMenu"}}
                     }),))
    # A terminal ID alone cannot select either existing owner when the
    # capture lacks a logical route or its owner is ambiguous.
    assert find_logical_menu_targets((first, second), _live_camera_selector_capture()) == ()
    assert len(find_logical_menu_targets((first, second), _logical_camera_selector_capture())) == 2


def test_same_item_id_under_two_menu_bars_uses_captured_owner():
    from dataclasses import replace
    first = _file_owner()
    first.subobjects["openrecordingmenuitem"] = {
        "kind": "menu_item", "criteria": {"id": "openRecordingMenuItem"},
    }
    second = replace(first, component_id="Edit", object_id="edit-owner",
                     strategies=(ComponentStrategy("javafx", {
                         "identification": {"mandatory": {"id": "editMenu"}}
                     }),), subobjects={"openrecordingmenuitem": {
                         "kind": "menu_item", "criteria": {"id": "openRecordingMenuItem"},
                     }})
    matches = find_logical_menu_targets((first, second), _new_file_menu_item_capture())
    assert len(matches) == 1
    assert matches[0].owner_component_id == first.component_id


def test_normalize_menu_subobjects_copies_canonical_shape():
    owner = _owner()
    normalized = normalize_menu_subobjects(owner.subobjects)
    assert normalized["camera"]["criteria"]["id"] == "cameraMenu"
    assert normalized["camera"]["ordinal"] == 0
    assert normalized["camera"]["subobjects"]["camera_selector"]["criteria"]["id"] == "cameraSelectorMenuItem"


def test_duplicate_live_skin_capture_still_maps_to_one_logical_item():
    owner = _owner()
    first = _logical_camera_selector_capture()
    second = CapturedComponent(**{**first.__dict__, "backend_properties": {
        **first.backend_properties, "node_ref": "n274", "bridge_pid": 4095,
    }})
    assert find_logical_menu_targets((owner,), first) == find_logical_menu_targets((owner,), second)


def test_new_menu_item_is_attached_under_existing_logical_menu_owner():
    owner = _file_owner()
    matches = find_logical_menu_targets((owner,), _new_file_menu_item_capture())
    assert len(matches) == 1
    match = matches[0]
    assert match.owner_component_id == owner.component_id
    assert match.subobject_path == ("openrecordingmenuitem",)
    assert owner.subobjects["openrecordingmenuitem"] == {
        "kind": "menu_item",
        "criteria": {"id": "openRecordingMenuItem", "text": "Open Recording"},
        "ordinal": 2,
        "relative_offset": {"x": 15.0, "y": 35.0, "tolerance": 8.0},
    }


def test_readable_menu_route_resolves_to_canonical_subobject_ids():
    assert resolve_authored_menu_route(_owner(), "Camera > Camera Selector") == (
        "camera", "camera_selector",
    )


def test_readable_menu_route_must_end_at_actionable_item():
    with pytest.raises(ValueError, match="terminate"):
        resolve_authored_menu_route(_owner(), "Camera")


def test_recording_matches_menu_item_to_owner_subobject_instead_of_top_level_object():
    owner = _owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    match = session._match(_logical_camera_selector_capture())
    assert match.status == "known_subobject"
    assert match.component_id == owner.object_id
    assert match.subobject_path == ("camera", "camera_selector")


def test_recording_attaches_new_item_to_existing_file_menu():
    owner = _file_owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    match = session._match(_new_file_menu_item_capture())
    assert match.status == "known_subobject"
    assert match.component_id == owner.object_id
    assert match.subobject_path == ("openrecordingmenuitem",)


def test_recorded_menu_subobject_becomes_select_menu_item_action():
    owner = _owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    capture = _logical_camera_selector_capture()
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
        "component_id": owner.object_id,
        "action": {
            "type": "select_menu_item",
            "path": ["camera", "camera_selector"],
        },
    }


def test_skin_pointer_and_logical_action_collapse_to_one_menu_interaction():
    owner = _owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    session.start()
    session.observe(PointerInteraction(
        1.0, "javafx", _live_camera_selector_capture(), {},
        "primary", "released", (100, 100),
    ))
    session.observe(ActionFired(
        1.05, "javafx", _logical_camera_selector_capture(), {}, "activate",
    ))
    interactions = session.stop()

    assert len(interactions) == 1
    interaction = interactions[0]
    assert interaction.target.native_class == "javafx.scene.control.MenuItem"
    assert interaction.repository_match.status == "known_subobject"
    assert interaction.repository_match.subobject_path == ("camera", "camera_selector")


def test_menu_open_and_intermediate_events_emit_only_terminal_route():
    owner = _owner()
    repository = ComponentRepository({owner.component_id: owner})
    opening = CapturedComponent(**{
        **_logical_camera_selector_capture().__dict__,
        "name": "Camera",
        "role": "menu",
        "accessible_id": "cameraMenu",
        "object_type": ObjectType.MENU,
        "backend_properties": {
            "logical_menu": {
                "path": [{"kind": "menu", "criteria": {"id": "cameraMenu", "text": "Camera"}}],
                "owner": {"kind": "context menu", "popup": {"role": "context menu"}},
            }
        },
    })
    terminal = _logical_camera_selector_capture()
    session = RecordingSession(repository=repository)
    session.start()
    session.observe(PointerInteraction(1.0, "javafx", opening, {}, "primary", "released", (100, 100)))
    session.observe(ActionFired(1.02, "javafx", opening, {}, "activate"))
    session.observe(PointerInteraction(1.1, "javafx", terminal, {}, "primary", "released", (110, 130)))
    session.observe(ActionFired(1.12, "javafx", terminal, {}, "activate"))

    interactions = session.stop()
    assert len(interactions) == 1
    assert interactions[0].repository_match.component_id == owner.object_id
    assert interactions[0].repository_match.subobject_path == ("camera", "camera_selector")
    assert interactions_to_steps(interactions)[0].inputs == {
        "component_id": owner.object_id,
        "action": {"type": "select_menu_item", "path": ["camera", "camera_selector"]},
    }


def test_abandoned_menu_route_is_visible_for_review():
    opening = CapturedComponent(**{
        **_logical_camera_selector_capture().__dict__,
        "name": "Camera",
        "role": "menu",
        "accessible_id": "cameraMenu",
        "object_type": ObjectType.MENU,
        "backend_properties": {
            "logical_menu": {
                "path": [{"kind": "menu", "criteria": {"id": "cameraMenu"}}],
                "owner": {"kind": "context menu", "popup": {"role": "context menu"}},
            }
        },
    })
    session = RecordingSession(repository=ComponentRepository({}))
    session.start()
    session.observe(PointerInteraction(1.0, "javafx", opening, {}, "primary", "released", (100, 100)))
    interactions = session.stop()
    assert len(interactions) == 1
    assert interactions[0].repository_match.status == "unresolved"
