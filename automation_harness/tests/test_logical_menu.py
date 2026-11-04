from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.logical_menu import (
    attach_context_menu_invoker,
    durable_menu_criteria,
    find_logical_menu_targets,
    is_javafx_menu_skin_capture,
    logical_menu_target_is_persisted,
    menu_action_payload,
    normalize_menu_subobjects,
    stage_recorded_menu_capture,
)
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.recording.observations import ActionFired, KeyboardInput, PointerInteraction, StateChanged
from automation_harness.recording.session import RecordedInteraction, RecordingSession, RepositoryMatch, interactions_to_steps


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
                "path": [{"kind": "menu item", "criteria": {"id": "cameraSelectorMenuItem", "text": "Camera Selector"}}],
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


def _file_menu_capture():
    return CapturedComponent(
        name="File",
        role="menu",
        description=None,
        accessible_id="fileMenu",
        application="SampleDisplay",
        window="SampleDisplay",
        hierarchy=(),
        actions=("click", "activate"),
        bounds=(10, 10, 60, 20),
        state=ComponentState(present=True, visible=True, showing=True, enabled=True),
        backend_properties={},
        object_type=ObjectType.MENU,
        framework="javafx",
        native_class="javafx.scene.control.Menu",
    )


def _plain_button_capture(name="Apply"):
    return CapturedComponent(
        name=name,
        role="button",
        description=None,
        accessible_id=name.casefold(),
        application="SampleDisplay",
        window="SampleDisplay",
        hierarchy=(),
        actions=("click",),
        bounds=(100, 100, 60, 25),
        state=ComponentState(present=True, visible=True, showing=True, enabled=True),
        backend_properties={},
        object_type=ObjectType.BUTTON,
        framework="atspi",
        native_class="javax.swing.JButton",
    )


def _choice_box_capture(name):
    return CapturedComponent(
        name=name,
        role="combo box",
        description=None,
        accessible_id=name.casefold(),
        application="SampleDisplay",
        window="SampleDisplay",
        hierarchy=(),
        actions=("click", "select_item"),
        bounds=(100, 100, 120, 25),
        state=ComponentState(present=True, visible=True, showing=True, enabled=True),
        backend_properties={},
        object_type=ObjectType.COMBO_BOX,
        framework="javafx",
        native_class="javafx.scene.control.ChoiceBox",
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
    # Matching normalizes a copy; it must not rewrite persisted repository data.
    assert owner.subobjects["camera"]["selector"]["criteria"]["id"] == "cameraMenu"
    assert "criteria" not in owner.subobjects["camera"]


def test_normalize_menu_subobjects_accepts_legacy_and_runtime_shapes():
    owner = _owner()
    normalized = normalize_menu_subobjects(owner.subobjects)
    assert normalized["camera"]["criteria"]["id"] == "cameraMenu"
    assert normalized["camera"]["ordinal"] == 0
    assert normalized["camera"]["subobjects"]["camera_selector"]["criteria"]["id"] == "cameraSelectorMenuItem"


def test_normalize_preserves_ordinal_and_relative_offset_from_legacy_selector():
    normalized = normalize_menu_subobjects({
        "open": {
            "kind": "menu_item",
            "selector": {
                "criteria": {"text": "Open"},
                "ordinal": 2,
                "relative_offset": {"x": 15.0, "y": 35.0, "tolerance": 8.0},
            },
        }
    })
    assert normalized["open"]["ordinal"] == 2
    assert normalized["open"]["relative_offset"] == {
        "x": 15.0, "y": 35.0, "tolerance": 8.0,
    }


def test_duplicate_live_skin_capture_still_maps_to_one_logical_item():
    owner = _owner()
    first = _live_camera_selector_capture()
    second = CapturedComponent(**{**first.__dict__, "backend_properties": {"node_ref": "n274", "bridge_pid": 4095}})
    assert find_logical_menu_targets((owner,), first) == find_logical_menu_targets((owner,), second)


def test_new_menu_item_matching_is_pure_until_review_stages_inventory():
    owner = _file_owner()
    repository = ComponentRepository({owner.component_id: owner})
    capture = _new_file_menu_item_capture()

    matches = find_logical_menu_targets((owner,), capture)

    assert len(matches) == 1
    match = matches[0]
    assert match.owner_component_id == owner.component_id
    assert match.subobject_path == ("openrecordingmenuitem",)
    assert owner.subobjects == {}
    assert not logical_menu_target_is_persisted(repository, match)

    updated, staged, owner_created, inventory_changed = stage_recorded_menu_capture(
        repository, capture,
    )

    assert owner_created is False
    assert inventory_changed is True
    assert staged == match
    assert logical_menu_target_is_persisted(updated, staged)
    stored = updated.get(owner.component_id).subobjects["openrecordingmenuitem"]
    assert stored["kind"] == "menu_item"
    assert stored["display_name"] == "Open Recording"
    assert stored["criteria"] == {
        "id": "openRecordingMenuItem",
        "text": "Open Recording",
    }
    assert stored["ordinal"] == 2
    assert stored["relative_offset"] == {
        "x": 15.0, "y": 35.0, "tolerance": 8.0,
    }


def test_recording_matches_menu_item_to_owner_subobject_instead_of_top_level_object():
    owner = _owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    match = session._match(_live_camera_selector_capture("javafx.scene.control.MenuItem"))
    assert match.status == "known_subobject"
    assert match.component_id == owner.component_id
    assert match.subobject_path == ("camera", "camera_selector")


def test_recording_previews_new_item_without_mutating_existing_file_menu():
    owner = _file_owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    match = session._match(_new_file_menu_item_capture())
    assert match.status == "known_subobject"
    assert match.component_id == owner.component_id
    assert match.subobject_path == ("openrecordingmenuitem",)
    assert owner.subobjects == {}


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


def test_recorded_menu_uses_readable_navigation_when_review_supplies_it():
    owner = _owner()
    capture = _live_camera_selector_capture("javafx.scene.control.MenuItem")
    interaction = RecordedInteraction(
        ActionType.CLICK,
        capture,
        {},
        1.0,
        1.0,
        evidence={"menu_navigation": "Camera > Camera Selector"},
        repository_match=RepositoryMatch(
            "known_subobject",
            (owner.component_id,),
            ("camera", "camera_selector"),
        ),
    )

    step = interactions_to_steps((interaction,))[0]

    assert step.inputs["action"]["path"] == ["camera", "camera_selector"]
    assert "Camera > Camera Selector" in step.description


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


def test_menu_opener_and_terminal_item_record_as_one_semantic_interaction():
    owner = _file_owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    session.start()

    session.observe(PointerInteraction(
        1.0, "javafx", _file_menu_capture(), {},
        "primary", "released", (20, 15),
    ))
    session.observe(PointerInteraction(
        1.2, "javafx", _new_file_menu_item_capture(), {},
        "primary", "released", (40, 80),
    ))
    interactions = session.stop()

    assert len(interactions) == 1
    assert interactions[0].repository_match.status == "known_subobject"
    step = interactions_to_steps(interactions)[0]
    assert step.inputs["component_id"] == owner.component_id
    assert step.inputs["action"]["type"] == "select_menu_item"
    assert step.inputs["action"]["path"] == list(interactions[0].repository_match.subobject_path)


def test_javafx_transient_pointer_miss_does_not_cancel_open_menu_transaction():
    owner = _file_owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    session.start()

    session.observe(PointerInteraction(
        1.0, "javafx", _file_menu_capture(), {},
        "primary", "released", (20, 15),
    ))
    # The popup has already collapsed by the time this physical release is
    # resolved, so the bridge sees the covered JavaFX control instead.
    covered = CapturedComponent(**{
        **_plain_button_capture("Covered").__dict__,
        "framework": "javafx",
        "native_class": "javafx.scene.control.Button",
    })
    session.observe(PointerInteraction(
        1.1, "javafx", covered, {},
        "primary", "released", (40, 80),
    ))
    session.observe(ActionFired(
        1.15, "javafx", _new_file_menu_item_capture(), {}, "activate",
    ))
    interactions = session.stop()

    assert len(interactions) == 1
    assert interactions[0].repository_match.status == "known_subobject"
    assert interactions[0].repository_match.subobject_path == ("openrecordingmenuitem",)


def test_click_outside_open_menu_discards_menu_opener_and_records_outside_click():
    owner = _file_owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    session.start()

    session.observe(PointerInteraction(
        1.0, "javafx", _file_menu_capture(), {},
        "primary", "released", (20, 15),
    ))
    session.observe(PointerInteraction(
        1.2, "atspi", _plain_button_capture(), {},
        "primary", "released", (120, 110),
    ))
    interactions = session.stop()

    assert len(interactions) == 1
    assert interactions[0].target.name == "Apply"
    assert interactions[0].action == ActionType.CLICK


def test_stale_javafx_menu_context_closes_and_reprocesses_unrelated_control():
    owner = _file_owner()
    session = RecordingSession(repository=ComponentRepository({owner.component_id: owner}))
    session.start()

    session.observe(PointerInteraction(
        1.0, "javafx", _file_menu_capture(), {},
        "primary", "released", (20, 15),
    ))
    unrelated = CapturedComponent(**{
        **_plain_button_capture("Apply").__dict__,
        "framework": "javafx",
        "native_class": "javafx.scene.control.Button",
    })
    session.observe(PointerInteraction(
        2.0, "javafx", unrelated, {},
        "primary", "released", (120, 110),
    ))
    interactions = session.stop()

    assert len(interactions) == 1
    assert interactions[0].target.name == "Apply"
    assert interactions[0].action == ActionType.CLICK
    assert session._menu_context is None


def test_committed_popup_context_does_not_consume_next_selector():
    first = _choice_box_capture("Site Selector")
    second = _choice_box_capture("Device Selector")
    session = RecordingSession()
    session.start()

    session.observe(PointerInteraction(
        1.0, "javafx", first, {},
        "primary", "released", (120, 110),
    ))
    session.observe(StateChanged(
        1.1, "javafx", first, {},
        property="selected_item", before=None, after="Site A",
    ))
    session.observe(PointerInteraction(
        1.3, "javafx", second, {},
        "primary", "released", (120, 150),
    ))

    assert session._popup_selection_context is not None
    assert session._popup_selection_context.owner.name == "Device Selector"

    session.observe(StateChanged(
        1.4, "javafx", second, {},
        property="selected_item", before=None, after="Device B",
    ))
    interactions = session.stop()

    assert len(interactions) == 2
    assert [item.action for item in interactions] == [ActionType.SELECT_ITEM, ActionType.SELECT_ITEM]
    assert [item.parameters["value"] for item in interactions] == ["Site A", "Device B"]


def test_context_menu_activation_absorbs_right_click_when_menu_is_cancelled():
    button = _plain_button_capture("Track")
    context_menu = CapturedComponent(
        name="Track Actions",
        role="context menu",
        description=None,
        accessible_id="track-actions",
        application="SampleDisplay",
        window="SampleDisplay",
        hierarchy=(),
        actions=("select_menu_item",),
        bounds=(110, 110, 160, 200),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={},
        object_type=ObjectType.CONTEXT_MENU,
        framework="atspi",
        native_class="javax.swing.JPopupMenu",
    )
    session = RecordingSession()
    session.start()

    session.observe(PointerInteraction(
        1.0, "atspi", button, {},
        "secondary", "released", (120, 110),
    ))
    session.observe(StateChanged(
        1.1, "atspi", context_menu, {},
        property="showing", before=False, after=True,
    ))
    session.observe(KeyboardInput(
        1.2, "x11", None, {},
        key="Escape", phase="pressed",
    ))
    interactions = session.stop()

    assert interactions == ()



def test_context_menu_selection_carries_invoking_object_evidence():
    invoking = _plain_button_capture("Track")
    context_menu = CapturedComponent(
        name="Track Actions",
        role="context menu",
        description=None,
        accessible_id="track-actions",
        application="SampleDisplay",
        window="SampleDisplay",
        hierarchy=(),
        actions=("select_menu_item",),
        bounds=(110, 110, 160, 200),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={},
        object_type=ObjectType.CONTEXT_MENU,
        framework="atspi",
        native_class="javax.swing.JPopupMenu",
    )
    terminal = _logical_camera_selector_capture()
    session = RecordingSession()
    session.start()

    session.observe(PointerInteraction(
        1.0, "atspi", invoking, {},
        "secondary", "released", (120, 110),
    ))
    session.observe(StateChanged(
        1.1, "atspi", context_menu, {},
        property="showing", before=False, after=True,
    ))
    session.observe(PointerInteraction(
        1.2, "javafx", terminal, {},
        "primary", "released", (130, 140),
    ))
    interactions = session.stop()

    assert len(interactions) == 1
    evidence = interactions[0].evidence
    assert evidence["menu_owner_capture"].object_type == ObjectType.CONTEXT_MENU
    assert evidence["menu_invoking_capture"].name == "Track"


def test_context_menu_owner_is_parented_to_concrete_invoking_object():
    invoker = ComponentDefinition(
        component_id="Track Button",
        strategies=(ComponentStrategy("atspi", {
            "identification": {"mandatory": {"accessible_id": "track"}},
        }),),
        actions=frozenset({"resolve", "activate"}),
        object_type=ObjectType.BUTTON,
    )
    menu = ComponentDefinition(
        component_id="Track Actions",
        strategies=(ComponentStrategy("atspi", {
            "identification": {"mandatory": {"accessible_id": "track-actions"}},
        }),),
        actions=frozenset({"resolve", "select_menu_item"}),
        object_type=ObjectType.CONTEXT_MENU,
    )
    repository = ComponentRepository({
        invoker.component_id: invoker,
        menu.component_id: menu,
    })

    updated, changed = attach_context_menu_invoker(
        repository, menu.component_id, invoker.component_id,
    )

    attached = updated.get(menu.component_id)
    assert changed is True
    assert attached.owner_object_id == invoker.object_id
    assert attached.properties["invoking_object_name"] == invoker.component_id


def test_ordinary_menu_is_not_reparented_as_context_menu_invocation():
    invoker = ComponentDefinition(
        component_id="Toolbar Button",
        strategies=(ComponentStrategy("atspi", {
            "identification": {"mandatory": {"accessible_id": "toolbar-button"}},
        }),),
        object_type=ObjectType.BUTTON,
    )
    menu = ComponentDefinition(
        component_id="File Menu",
        strategies=(ComponentStrategy("atspi", {
            "identification": {"mandatory": {"accessible_id": "file-menu"}},
        }),),
        object_type=ObjectType.MENU,
    )
    repository = ComponentRepository({
        invoker.component_id: invoker,
        menu.component_id: menu,
    })

    updated, changed = attach_context_menu_invoker(
        repository, menu.component_id, invoker.component_id,
    )

    assert changed is False
    assert updated.get(menu.component_id).owner_object_id is None


def _logical_owner_menu(component_id, menu_id, label, child_label="Settings"):
    return ComponentDefinition(
        component_id=component_id,
        description=label,
        framework="javafx",
        object_type=ObjectType.MENU,
        actions=frozenset({"resolve", "activate", "select_menu_item"}),
        strategies=(ComponentStrategy("javafx", {
            "identification": {
                "mandatory": {"id": menu_id},
                "assistive": {"text": label},
            }
        }),),
        subobjects={
            "settings": {
                "kind": "menu_item",
                "criteria": {"id": "settingsMenuItem", "text": child_label},
            },
        },
    )


def _owned_menu_capture(owner_id, owner_label, item_label="Settings"):
    base = _live_camera_selector_capture("javafx.scene.control.MenuItem")
    logical_menu = {
        "owner": {
            "kind": "menu",
            "logical": {
                "kind": "menu",
                "criteria": {"id": owner_id, "text": owner_label},
            },
        },
        "path": [
            {"kind": "menu", "criteria": {"id": owner_id, "text": owner_label}},
            {"kind": "menu_item", "criteria": {"id": "settingsMenuItem", "text": item_label}},
        ],
    }
    return CapturedComponent(**{
        **base.__dict__,
        "name": item_label,
        "accessible_id": "settingsMenuItem",
        "backend_properties": {"logical_menu": logical_menu},
    })


def test_system_menu_capture_is_scoped_to_logical_owner_not_matching_file_item():
    file_menu = _logical_owner_menu("file-owner", "fileMenu", "File")
    system_menu = _logical_owner_menu("system-owner", "systemMenu", "System")
    capture = _owned_menu_capture("systemMenu", "System")

    matches = find_logical_menu_targets((file_menu, system_menu), capture)

    assert len(matches) == 1
    assert matches[0].owner_component_id == "system-owner"
    assert matches[0].subobject_path == ("settings",)


def test_duplicate_owner_ids_are_disambiguated_by_menu_text():
    # Duplicate IDs are encountered in the JavaFX AUT. Corroborating owner
    # text prevents the File menu from receiving System menu interactions.
    file_menu = _logical_owner_menu("file-owner", "sharedMenu", "File")
    system_menu = _logical_owner_menu("system-owner", "sharedMenu", "System")
    capture = _owned_menu_capture("sharedMenu", "System")

    matches = find_logical_menu_targets((file_menu, system_menu), capture)

    assert len(matches) == 1
    assert matches[0].owner_component_id == "system-owner"


def test_unknown_or_ambiguous_menu_owner_does_not_fall_back_to_global_item():
    file_menu = _logical_owner_menu("file-owner", "fileMenu", "File")
    unrecognized = _owned_menu_capture("systemMenu", "System")
    assert find_logical_menu_targets((file_menu,), unrecognized) == ()

    system_menu = _logical_owner_menu("system-owner", "systemMenu", "System")
    duplicate = _logical_owner_menu("other-system-owner", "systemMenu", "System")
    assert find_logical_menu_targets((system_menu, duplicate), unrecognized) == ()


def test_duplicate_child_ids_with_different_text_do_not_overwrite_siblings():
    owner = _file_owner()
    repository = ComponentRepository({owner.component_id: owner})
    original = _new_file_menu_item_capture()
    repository, first, _, changed = stage_recorded_menu_capture(repository, original)
    assert changed and first is not None

    metadata = original.backend_properties["logical_menu"]
    updated_path = [dict(item) for item in metadata["path"]]
    updated_path[-1] = dict(updated_path[-1])
    updated_path[-1]["criteria"] = {
        "id": "openRecordingMenuItem",
        "text": "Open Logs",
    }
    other = CapturedComponent(**{
        **original.__dict__,
        "name": "Open Logs",
        "backend_properties": {
            "logical_menu": {**metadata, "path": updated_path},
        },
    })
    repository, second, _, changed = stage_recorded_menu_capture(repository, other)

    assert changed and second is not None
    assert first.subobject_path != second.subobject_path
    stored = repository.get(owner.component_id).subobjects
    assert stored[first.subobject_path[0]]["criteria"]["text"] == "Open Recording"
    assert stored[second.subobject_path[0]]["criteria"]["text"] == "Open Logs"
