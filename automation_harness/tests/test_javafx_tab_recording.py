"""Owner-scoped JavaFX TabPane recording contract and playback parity."""
from automation_harness.authoring.recording_review import materialize_recorded_interaction
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import ComponentDefinition
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.recording.adapters.javafx import JavaFxRecordingAdapter
from automation_harness.recording.observations import PointerInteraction, StateChanged
from automation_harness.recording.session import RecordingSession, interactions_to_steps


class _Transport:
    def request(self, operation, payload):
        return {"observations": []}


def _owner():
    return {
        "class": "edu.mit.ll.ersa.common.dashboard.components.AdvancedDetailsDisplay",
        "id": "advancedDetailsTabs",
        "accessible_role": "TAB_PANE",
        "window": "Advanced Details",
        "tabs": [
            {"index": 0, "id": None, "text": "Camera Status"},
            {"index": 3, "id": "sessionViewTab", "text": "Active Sessions"},
        ],
    }


def _event(tab):
    return {
        "type": "state_changed",
        "timestamp": 1.5,
        "property": "tab_selection",
        "before": None,
        "after": tab,
        "target": {"node": _owner()},
    }


def _repository(owner):
    definition = ComponentDefinition(
        "advanced.details.tabs",
        object_type=ObjectType.TAB_CONTAINER,
        framework="javafx",
        native_class=owner.native_class,
        actions=frozenset({"resolve", "select_item"}),
        strategies=(owner.candidate_strategy(),),
    )
    return ComponentRepository({definition.component_id: definition})


def test_javafx_tab_event_normalizes_to_owner_semantics():
    event = JavaFxRecordingAdapter(_Transport()).normalize(
        _event({"id": "sessionViewTab", "text": "Active Sessions", "index": 3})
    )
    assert isinstance(event, StateChanged)
    assert event.target.semantic_type() is ObjectType.TAB_CONTAINER
    assert event.target.backend_properties["tabs"][0]["text"] == "Camera Status"
    assert event.after["id"] == "sessionViewTab"


def test_tab_change_creates_one_semantic_step_not_an_owner_click():
    adapter = JavaFxRecordingAdapter(_Transport())
    observation = adapter.normalize(
        _event({"id": "sessionViewTab", "text": "Active Sessions", "index": 3})
    )
    repository = _repository(observation.target)
    session = RecordingSession(repository=repository)
    session.start()
    session.observe(PointerInteraction(
        1.0, "javafx", observation.target, phase="released", coordinates=(50, 25)
    ))
    session.observe(observation)
    interactions = session.stop()

    assert len(interactions) == 1
    assert interactions[0].action is ActionType.SELECT_ITEM
    assert interactions[0].repository_match.status == "known_unique"
    step = interactions_to_steps(interactions)[0]
    assert step.inputs["component_id"] == repository.get("advanced.details.tabs").object_id
    assert step.inputs["action"] == {
        "type": "select_item",
        "selector": {"kind": "tab", "criteria": {
            "id": "sessionViewTab", "text": "Active Sessions", "index": 3,
        }},
    }
    reviewed = materialize_recorded_interaction(repository, interactions[0])
    assert reviewed.error is None
    assert reviewed.resolved
    assert reviewed.inventory_changed
    owner = reviewed.repository.get("advanced.details.tabs")
    assert owner.object_id == repository.get("advanced.details.tabs").object_id
    assert owner.object_type is ObjectType.TAB_CONTAINER
    assert owner.subobjects["sessionviewtab"]["criteria"] == {
        "id": "sessionViewTab", "text": "Active Sessions",
    }
    assert owner.subobjects["camera_status"]["criteria"] == {"text": "Camera Status"}


def test_idless_camera_status_records_owner_scoped_text_selector():
    adapter = JavaFxRecordingAdapter(_Transport())
    observation = adapter.normalize(_event({"text": "Camera Status", "index": 0}))
    session = RecordingSession(repository=_repository(observation.target))
    session.start()
    session.observe(observation)
    step = interactions_to_steps(session.stop())[0]
    assert step.inputs["action"]["selector"] == {
        "kind": "tab", "criteria": {"text": "Camera Status", "index": 0},
    }


def test_tab_selection_without_semantic_identity_is_not_recorded():
    adapter = JavaFxRecordingAdapter(_Transport())
    observation = adapter.normalize(_event({"index": 0}))
    session = RecordingSession()
    session.start()
    session.observe(observation)
    assert session.stop() == ()
