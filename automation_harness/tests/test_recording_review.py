from automation_harness.authoring.recording_review import (
    materialize_recorded_interaction,
)
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import (
    CapturedComponent,
    ComponentDefinition,
    ComponentState,
    ComponentStrategy,
)
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.recording.session import RecordedInteraction, RepositoryMatch


def _state():
    return ComponentState(
        present=True,
        visible=True,
        showing=True,
        enabled=True,
    )


def _file_owner():
    return ComponentDefinition(
        component_id="File Menu",
        strategies=(
            ComponentStrategy(
                "javafx",
                {
                    "identification": {
                        "mandatory": {"id": "fileMenu"},
                        "assistive": {"text": "File"},
                    }
                },
            ),
        ),
        actions=frozenset({"resolve", "select_menu_item"}),
        object_type=ObjectType.MENU,
        framework="javafx",
        native_class="javafx.scene.control.Menu",
        subobjects={},
    )


def _new_file_item():
    return CapturedComponent(
        name="Open Recording",
        role="menu item",
        description=None,
        accessible_id="openRecordingMenuItem",
        application="MVD",
        window="MVD",
        hierarchy=(),
        actions=("activate",),
        bounds=(100, 100, 120, 24),
        state=_state(),
        backend_properties={
            "logical_menu": {
                "path": [
                    {
                        "kind": "menu",
                        "criteria": {"id": "fileMenu", "text": "File"},
                    },
                    {
                        "kind": "menu item",
                        "criteria": {
                            "id": "openRecordingMenuItem",
                            "text": "Open Recording",
                        },
                    },
                ],
                "owner": {
                    "kind": "menu",
                    "logical": {
                        "kind": "menu",
                        "criteria": {"id": "fileMenu", "text": "File"},
                    },
                },
            }
        },
        authored_strategy=ComponentStrategy(
            "javafx",
            {
                "identification": {
                    "mandatory": {"id": "openRecordingMenuItem"},
                    "assistive": {"text": "Open Recording"},
                }
            },
        ),
        object_type=ObjectType.MENU_ITEM,
        framework="javafx",
        native_class="javafx.scene.control.MenuItem",
    )


def _context_owner_capture():
    return CapturedComponent(
        name="Track Actions",
        role="context menu",
        description=None,
        accessible_id="trackActions",
        application="MVD",
        window="MVD",
        hierarchy=(),
        actions=("select_menu_item",),
        bounds=(100, 100, 180, 240),
        state=_state(),
        backend_properties={},
        authored_strategy=ComponentStrategy(
            "javafx",
            {"identification": {"mandatory": {"id": "trackActions"}}},
        ),
        object_type=ObjectType.CONTEXT_MENU,
        framework="javafx",
        native_class="javafx.scene.control.ContextMenu",
        logical_subobjects={
            "delete": {
                "kind": "menu_item",
                "display_name": "Delete",
                "criteria": {"id": "deleteItem", "text": "Delete"},
                "ordinal": 0,
                "selectable": True,
            }
        },
    )


def _context_terminal():
    return CapturedComponent(
        name="Delete",
        role="menu item",
        description=None,
        accessible_id="deleteItem",
        application="MVD",
        window="MVD",
        hierarchy=(),
        actions=("activate",),
        bounds=(110, 130, 120, 24),
        state=_state(),
        backend_properties={},
        authored_strategy=ComponentStrategy(
            "javafx",
            {
                "identification": {
                    "mandatory": {"id": "deleteItem"},
                    "assistive": {"text": "Delete"},
                }
            },
        ),
        object_type=ObjectType.MENU_ITEM,
        framework="javafx",
        native_class="javafx.scene.control.MenuItem",
    )


def _invoker():
    return CapturedComponent(
        name="Track",
        role="button",
        description=None,
        accessible_id="trackButton",
        application="MVD",
        window="MVD",
        hierarchy=(),
        actions=("click", "focus"),
        bounds=(20, 20, 80, 24),
        state=_state(),
        backend_properties={},
        authored_strategy=ComponentStrategy(
            "javafx",
            {"identification": {"mandatory": {"id": "trackButton"}}},
        ),
        object_type=ObjectType.BUTTON,
        framework="javafx",
        native_class="javafx.scene.control.Button",
    )


def test_review_stages_new_menu_item_under_existing_owner():
    owner = _file_owner()
    repository = ComponentRepository({owner.component_id: owner})
    target = _new_file_item()
    interaction = RecordedInteraction(
        ActionType.CLICK,
        target,
        {},
        1.0,
        1.0,
        repository_match=RepositoryMatch(
            "known_subobject",
            (owner.component_id,),
            ("openrecordingmenuitem",),
        ),
    )

    outcome = materialize_recorded_interaction(repository, interaction)

    assert outcome.error is None
    assert outcome.inventory_changed is True
    assert outcome.interaction.repository_match.status == "known_subobject"
    assert outcome.interaction.repository_match.subobject_path == (
        "openrecordingmenuitem",
    )
    stored = outcome.repository.get(owner.component_id)
    assert stored.subobjects["openrecordingmenuitem"]["display_name"] == "Open Recording"
    assert outcome.interaction.evidence["menu_navigation"] == "Open Recording"


def test_review_materializes_new_context_menu_and_invoker_as_one_transaction():
    owner_capture = _context_owner_capture()
    terminal = _context_terminal()
    invoker = _invoker()
    interaction = RecordedInteraction(
        ActionType.CLICK,
        terminal,
        {},
        1.0,
        1.0,
        evidence={
            "menu_owner_capture": owner_capture,
            "menu_invoking_capture": invoker,
        },
        repository_match=RepositoryMatch("new_candidate"),
    )

    outcome = materialize_recorded_interaction(
        ComponentRepository({}),
        interaction,
    )

    assert outcome.error is None
    assert outcome.interaction.repository_match.status == "known_subobject"
    assert outcome.interaction.repository_match.subobject_path == ("delete",)
    menu_id = outcome.interaction.repository_match.component_id
    menu = outcome.repository.get(menu_id)
    invoking = outcome.repository.get("Track Button")
    assert menu.object_type == ObjectType.CONTEXT_MENU
    assert menu.owner_object_id == invoking.object_id
    assert menu.properties["invoking_object_name"] == "Track Button"
    assert outcome.interaction.evidence["menu_navigation"] == "Delete"


def test_review_failure_rolls_back_partial_repository_changes():
    owner_capture = _context_owner_capture()
    terminal = CapturedComponent(
        **{
            **_context_terminal().__dict__,
            "accessible_id": "missingItem",
            "name": "Missing",
        }
    )
    interaction = RecordedInteraction(
        ActionType.CLICK,
        terminal,
        {},
        1.0,
        1.0,
        evidence={"menu_owner_capture": owner_capture},
        repository_match=RepositoryMatch("new_candidate"),
    )
    original = ComponentRepository({})

    outcome = materialize_recorded_interaction(original, interaction)

    assert outcome.error is not None
    assert outcome.repository.components == {}
    assert outcome.interaction == interaction


def test_review_rejects_an_in_memory_definition_that_cannot_round_trip():
    broken = ComponentDefinition(
        component_id="Broken MenuButton",
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {}}}),),
        actions=frozenset({"resolve", "activate"}),
        object_type=ObjectType.MENU,
    )
    original = ComponentRepository({broken.component_id: broken})
    interaction = RecordedInteraction(
        ActionType.CLICK,
        _context_terminal(),
        {}, 1.0, 1.0,
        repository_match=RepositoryMatch("known_unique", (broken.component_id,)),
    )

    outcome = materialize_recorded_interaction(original, interaction)

    assert outcome.error is not None
    assert outcome.repository is original
    assert outcome.created_component_ids == ()


def test_review_retains_provisional_runtime_capture_without_creating_step():
    provisional = CapturedComponent(
        name="track@track-a",
        role="rendered_object",
        description=None,
        accessible_id=None,
        application="MSCT Domain 12",
        window="MSCT Domain 12",
        hierarchy=("AWTViewCanvas", "Track"),
        actions=("click",),
        bounds=(100, 200, 13, 13),
        state=_state(),
        backend_properties={"track_runtime_ref": "track-a"},
        authored_strategy=ComponentStrategy(
            "java_agent",
            {"runtime_correlation": {"track_runtime_ref": "track-a"}},
        ),
        object_type=ObjectType.CUSTOM,
        framework="solipsys_rendered",
        native_class="com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
    )
    interaction = RecordedInteraction(
        ActionType.CLICK,
        provisional,
        {},
        1.0,
        1.0,
        repository_match=RepositoryMatch("new_candidate"),
    )

    outcome = materialize_recorded_interaction(ComponentRepository({}), interaction)

    assert outcome.error is not None
    assert outcome.provisional_component_ids
    assert outcome.created_component_ids == outcome.provisional_component_ids
    assert outcome.repository.components
    retained = outcome.repository.get(outcome.provisional_component_ids[0])
    assert retained.properties["locator_status"] == "provisional"


def test_review_rejects_zero_confidence_interaction_without_repository_mutation():
    target = _invoker()
    interaction = RecordedInteraction(
        ActionType.CLICK,
        target,
        {},
        1.0,
        1.0,
        confidence=0.0,
        repository_match=RepositoryMatch("unresolved"),
    )
    original = ComponentRepository({})

    outcome = materialize_recorded_interaction(original, interaction)

    assert outcome.error is not None
    assert "zero confidence" in str(outcome.error)
    assert outcome.repository is original
    assert outcome.created_component_ids == ()


def test_review_rejects_failed_menu_route_instead_of_leaking_opener_click():
    target = CapturedComponent(
        name="File",
        role="menu",
        description=None,
        accessible_id="fileMenu",
        application="MVD",
        window="MVD",
        hierarchy=("AnchorPane#AnchorPane", "MenuBar", "HBox", "MenuBarButton#fileMenu"),
        actions=("activate", "click"),
        bounds=(9, 65, 43, 29),
        state=_state(),
        backend_properties={"text": "File"},
        authored_strategy=ComponentStrategy(
            "javafx",
            {"identification": {"mandatory": {"id": "fileMenu"}}},
        ),
        object_type=ObjectType.MENU,
        framework="javafx",
        native_class="com.sun.javafx.scene.control.MenuBarButton",
    )
    interaction = RecordedInteraction(
        ActionType.CLICK,
        target,
        {},
        1.0,
        1.0,
        evidence={"menu_route_error": "focus_left_menu"},
        confidence=1.0,
        repository_match=RepositoryMatch("unresolved"),
    )
    original = ComponentRepository({})

    outcome = materialize_recorded_interaction(original, interaction)

    assert outcome.error is not None
    assert "failed menu route" in str(outcome.error)
    assert outcome.repository is original
    assert outcome.created_component_ids == ()
