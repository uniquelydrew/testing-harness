"""Verify the unmodified main popup recording pipeline through review/preflight."""
import pytest

from automation_harness.authoring.recording_review import materialize_recorded_interaction
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_plan import validate_plan_components
from automation_harness.drivers.javafx_bridge import _captured_recording_node
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.models.plan import TestPlan
from automation_harness.recording import RecordingSession
from automation_harness.recording.observations import PointerInteraction
from automation_harness.recording.session import interactions_to_steps


@pytest.mark.parametrize("control_class,popup_class,popup_role", [
    ("javafx.scene.control.ComboBox", "javafx.scene.control.ListCell", "list item"),
    ("javafx.scene.control.ChoiceBox", "javafx.scene.control.MenuItem", "menu item"),
])
def test_popup_selection_survives_recording_review_and_preflight(
    control_class, popup_class, popup_role,
):
    owner = {
        "name": "Camera", "role": "combo box", "class": control_class,
        "id": "camera", "window": "Main", "bounds": [10, 10, 100, 25],
    }
    popup_item = {
        "name": "North", "role": popup_role, "class": popup_class,
        "window": "Popup",
        "popup_selection": {
            "index": 1, "value": "North", "family": "popup_selector",
            "owner": owner,
        },
    }
    captured_owner = _captured_recording_node(owner)
    captured_selection = _captured_recording_node(popup_item)

    repository = ComponentRepository({})
    session = RecordingSession(repository=repository)
    session.start()
    session.observe(PointerInteraction(
        0.5, "javafx", captured_owner, {}, "primary", "released", (20, 20),
    ))
    session.observe(PointerInteraction(
        1.0, "javafx", captured_selection, {}, "primary", "released", (20, 45),
    ))
    interactions = session.stop()

    assert len(interactions) == 1
    assert interactions[0].action is ActionType.SELECT_ITEM
    assert interactions[0].target.semantic_type() is ObjectType.COMBO_BOX
    assert interactions[0].parameters == {"value": "North"}

    review = materialize_recorded_interaction(
        repository, interactions[0], recording_repository=repository,
    )
    assert review.error is None, str(review.error)
    assert review.resolved
    assert review.repository.get(
        review.interaction.repository_match.component_id,
    ).object_type is ObjectType.COMBO_BOX

    call = interactions_to_steps((review.interaction,))[0]
    assert call.inputs["action"] == {"type": "select_item", "value": "North"}
    assert validate_plan_components(
        TestPlan(name="popup recording", steps=(call,)), review.repository,
    ) == []
