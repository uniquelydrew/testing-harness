from automation_harness.drivers.javafx_bridge import JavaFxRecordingBridge, _captured_recording_node


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


def test_mixed_agent_swing_snapshot_authors_native_agent_locator():
    capture = _captured_recording_node({
        "framework": "swing",
        "class": "com.jogamp.opengl.awt.GLCanvas",
        "native_class": "com.jogamp.opengl.awt.GLCanvas",
        "name": "Tactical Display",
        "accessible_id": "tactical-display",
        "window": "MSCT Display",
        "role": "canvas",
        "object_type": "canvas",
        "bounds": [100, 200, 800, 600],
        "component_path": "javax.swing.JFrame[0]/com.jogamp.opengl.awt.GLCanvas[0]",
        "properties": {"opaque_render_surface": True, "process_id": 42},
    })

    assert capture.framework == "swing"
    assert capture.native_class == "com.jogamp.opengl.awt.GLCanvas"
    assert capture.candidate_strategy().type == "java_agent"
    assert capture.candidate_strategy().options["identification"]["mandatory"] == {
        "accessible_id": "tactical-display",
        "native_class": "com.jogamp.opengl.awt.GLCanvas",
    }
    assert capture.candidate_strategy().options["identification"]["assistive"]["component_path"].endswith(
        "GLCanvas[0]"
    )


def test_unnamed_same_class_swing_controls_keep_distinct_resolvable_paths():
    first = _captured_recording_node({
        "framework": "swing", "native_class": "javax.swing.JPanel", "role": "panel",
        "component_path": "javax.swing.JFrame[0]/javax.swing.JPanel[0]",
    })
    second = _captured_recording_node({
        "framework": "swing", "native_class": "javax.swing.JPanel", "role": "panel",
        "component_path": "javax.swing.JFrame[0]/javax.swing.JPanel[1]",
    })

    assert first.candidate_strategy() != second.candidate_strategy()
    assert first.candidate_strategy().options["identification"]["mandatory"]["component_path"].endswith("[0]")
    assert second.candidate_strategy().options["identification"]["mandatory"]["component_path"].endswith("[1]")


def test_javafx_nested_tab_inventory_is_preserved_in_recorded_capture():
    node = {
        "framework": "javafx",
        "class": "javafx.scene.control.TabPane",
        "id": "childTabs",
        "window": "MSCT Display",
        "selected_index": 1,
        "tabs": [
            {"index": 0, "id": "configurationTab", "text": "Configuration",
             "selected": False, "disabled": False,
             "content_id": "configurationPane", "content_class": "javafx.scene.layout.Pane"},
            {"index": 1, "id": "statusTab", "text": "Status",
             "selected": True, "disabled": False,
             "content_id": "statusPane", "content_class": "javafx.scene.layout.Pane"},
        ],
    }

    capture = _captured_recording_node(node)

    assert capture.state.properties["selected_index"] == 1
    assert capture.backend_properties["tabs"] == node["tabs"]
    assert capture.semantic_type().value == "tab_container"


def test_javafx_select_child_preserves_bridge_tab_selection_metadata():
    from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver

    class Endpoint:
        pid = 4095

        def request(self, op, **kwargs):
            assert op == "select_child"
            assert kwargs["identification"] == {"mandatory": {"id": "childTabs"}}
            assert kwargs["index"] == 1
            return {"index": 1, "selection_family": "tab_pane", "node": {
                "id": "childTabs", "selected_index": 1,
            }}

    driver = JavaFxBridgeDriver()
    driver._find_unique = lambda identification: (Endpoint(), {}, ())
    result = driver.select_child(1, identification={"mandatory": {"id": "childTabs"}})

    assert result["index"] == 1
    assert result["selection_family"] == "tab_pane"
    assert result["node"]["selected_index"] == 1


def test_javafx_semantic_tab_selection_routes_owner_scoped_criteria_to_native_agent():
    from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver

    class Endpoint:
        pid = 4095

        def __init__(self):
            self.calls = []

        def request(self, op, **kwargs):
            self.calls.append((op, kwargs))
            return {
                "selection_family": "tab_pane",
                "index": 3,
                "tab": {"id": "sessionViewTab", "text": "Active Sessions"},
                "node": {"id": "advancedTabs"},
            }

    endpoint = Endpoint()
    driver = JavaFxBridgeDriver()
    driver._find_unique = lambda identification: (endpoint, {}, ())
    identity = {"mandatory": {"id": "advancedTabs"}}
    criteria = {"id": "sessionViewTab", "text": "Active Sessions"}
    result = driver.select_tab(criteria, identification=identity)

    assert result["index"] == 3
    assert result["tab"]["text"] == "Active Sessions"
    assert endpoint.calls == [("select_tab", {
        "timeout": 5.0, "identification": identity, "criteria": criteria,
    })]


def test_javafx_semantic_tab_selector_requires_identity_and_rejects_unscoped_index():
    import pytest
    from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver

    driver = JavaFxBridgeDriver()
    with pytest.raises(ValueError, match="ID or text"):
        driver.select_tab({"index": 3}, identification={"mandatory": {"id": "advancedTabs"}})
    with pytest.raises(ValueError, match="unsupported"):
        driver.select_tab({"text": "Camera Status", "window": "other"})
    with pytest.raises(ValueError, match="nonnegative"):
        driver.select_tab({"text": "Camera Status", "index": -1})


def test_native_java_accessibility_metadata_survives_bridge_without_becoming_identity():
    node = {
        "framework": "swing",
        "native_class": "org.example.trackstate.UiTrackStateDBStatusIndicator",
        "name": "Object",
        "accessible_name": "Object",
        "accessible_description": "Track state indicator",
        "accessible_role": "menu",
        "role": "menu",
        "object_type": "menu",
        "text": "Track state",
        "component_name": "trackStateIndicator",
        "tooltip_text": "Current track state",
        "component_path": "javax.swing.JFrame[0]/org.example.trackstate.UiTrackStateDBStatusIndicator[0]",
        "window": "Demo Display",
        "state": {"present": True, "selected_index": 2, "editable": False},
        "properties": {"process_id": 5908},
    }

    captured = _captured_recording_node(node)

    assert captured.name == "Object"
    assert captured.backend_properties["accessible_name"] == "Object"
    assert captured.backend_properties["accessible_description"] == "Track state indicator"
    assert captured.backend_properties["text"] == "Track state"
    assert captured.backend_properties["component_name"] == "trackStateIndicator"
    assert captured.backend_properties["capture_source"] == "native_java_agent"
    assert captured.description == "Track state indicator"
    assert captured.state.properties["selected_index"] == 2
    locator = captured.candidate_strategy().options["identification"]
    assert locator["mandatory"] == {
        "name": "Object",
        "native_class": node["native_class"],
    }
    assert "process_id" not in locator["mandatory"]
    assert "accessible_description" not in locator["mandatory"]
