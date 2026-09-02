from automation_harness.drivers.javafx_bridge import JavaFxRecordingBridge


class _Transport:
    def request(self, operation, payload):
        assert operation == "hit_test"
        assert payload == {"x": 12, "y": 18}
        return {
            "physical_node": {"class": "javafx.scene.text.Text", "text": "Save", "ref": "text-1", "role": "text"},
            "semantic_node": {"class": "javafx.scene.control.Button", "name": "Save", "role": "button", "accessible_id": "save", "actions": ["click"]},
            "promotion": {"promoted": True, "descendant_depth": 3, "reason": "interactive_ancestor"},
        }


def test_javafx_capture_uses_semantic_control_and_keeps_compact_physical_evidence():
    capture = JavaFxRecordingBridge(_Transport()).hit_test(12, 18)
    assert capture.semantic_type().value == "button"
    assert capture.name == "Save"
    assert capture.backend_properties["physical_target"] == {
        "class": "javafx.scene.text.Text", "text": "Save", "ref": "text-1",
        "descendant_depth": 3, "reason": "interactive_ancestor",
    }


def test_javafx_standalone_text_remains_a_semantic_label():
    resolution = JavaFxRecordingBridge(_Transport()).semantic_target({
        "node": {"class": "javafx.scene.text.Text", "text": "Status", "role": "label"},
        "promotion": {"promoted": False, "reason": "no_interactive_ancestor"},
    })
    assert resolution.capture().semantic_type().value == "label"
    assert "physical_target" not in resolution.capture().backend_properties
