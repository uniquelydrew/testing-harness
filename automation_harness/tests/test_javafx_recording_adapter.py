from automation_harness.recording.adapters.javafx import JavaFxRecordingAdapter
from automation_harness.recording.observations import PointerInteraction, TextChanged


class _Transport:
    def request(self, operation, payload):
        return {"observations": []}


def _target():
    return {
        "physical_node": {"class": "javafx.scene.text.Text", "text": "Save", "role": "text"},
        "semantic_node": {"class": "javafx.scene.control.Button", "name": "Save", "role": "button"},
        "promotion": {"promoted": True, "descendant_depth": 2, "reason": "interactive_ancestor"},
    }


def test_javafx_adapter_filters_noise_and_normalizes_semantic_target():
    adapter = JavaFxRecordingAdapter(_Transport())
    assert adapter.normalize({"type": "mouse_moved", "timestamp": 1}) is None
    event = adapter.normalize({"type": "pointer", "timestamp": 1, "target": _target(), "coordinates": [1, 2]})
    assert isinstance(event, PointerInteraction)
    assert event.target.semantic_type().value == "button"
    assert event.target.backend_properties["physical_target"]["class"] == "javafx.scene.text.Text"
    text = adapter.normalize({"type": "text_changed", "timestamp": 2, "target": _target(), "before": "", "after": "hello"})
    assert isinstance(text, TextChanged)
    assert text.after == "hello"
