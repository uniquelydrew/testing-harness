"""Small executable contracts for menu completion and combo ownership."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from automation_harness.core.completion import _infer_condition
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver, _captured_recording_node
from automation_harness.models.component import ComponentDefinition
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.models.plan import StepCall
from automation_harness.recording import RecordingSession
from automation_harness.recording.observations import PointerInteraction


class MenuComboRegression(unittest.TestCase):
    def test_menu_owner_survives_cancel_without_creating_a_step(self):
        owner = _captured_recording_node({
            "name": "File", "role": "menu", "object_type": "menu",
            "accessible_id": "fileMenu", "framework": "javafx", "window": "Main",
            "bounds": [10, 10, 40, 25],
        })
        session = RecordingSession()
        session.start()
        session.observe(PointerInteraction(1.0, "javafx", owner, {}, "primary", "released", (20, 20)))
        self.assertEqual(session.stop(), ())
        self.assertEqual(session.captured_menu_owners(), (owner,))

    def test_menu_completion_does_not_recheck_closed_owner(self):
        owner = ComponentDefinition(
            component_id="File Menu", object_type=ObjectType.MENU,
            expected_states={"visible": True, "showing": True},
        )
        context = type("Context", (), {"components": type("Components", (), {
            "contains": lambda self, key: key == "File Menu",
            "get": lambda self, key: owner,
        })()})()
        call = StepCall(node_id="menu", step_id="gui.object.action")
        self.assertIsNone(_infer_condition(context, call, {
            "component_id": "File Menu", "action": {"type": "select_menu_item"},
        }))

    def test_combo_popup_cell_is_owned_by_combo(self):
        owner = {"name": "Camera", "role": "combo box", "class": "javafx.scene.control.ComboBox",
                 "id": "camera", "window": "Main", "bounds": [10, 10, 100, 25]}
        cell = {"name": "North", "role": "list item", "class": "javafx.scene.control.ListCell",
                "window": "Popup", "combo_selection": {"index": 2, "owner": owner}}
        capture = _captured_recording_node(cell)
        self.assertEqual(capture.semantic_type(), ObjectType.COMBO_BOX)
        self.assertEqual(capture.candidate_strategy().type, "javafx")
        self.assertEqual(capture.backend_properties["combo_selection"]["index"], 2)
        session = RecordingSession()
        session.start()
        opener = replace(capture, backend_properties={
            key: value for key, value in capture.backend_properties.items() if key != "combo_selection"
        })
        session.observe(PointerInteraction(0.5, "javafx", opener, {}, "primary", "released", (20, 20)))
        session.observe(PointerInteraction(1.0, "javafx", capture, {}, "primary", "released", (20, 20)))
        session.observe(PointerInteraction(1.05, "javafx", capture, {}, "primary", "released", (20, 20)))
        interactions = session.stop()
        self.assertEqual(len(interactions), 1)
        self.assertEqual(interactions[0].action, ActionType.SELECT_ITEM)
        self.assertEqual(interactions[0].parameters, {"value": 2})

    def test_combo_completion_checks_selected_index(self):
        owner = ComponentDefinition(
            component_id="Camera", object_type=ObjectType.COMBO_BOX,
            expected_states={"visible": True},
        )
        context = type("Context", (), {"components": type("Components", (), {
            "contains": lambda self, key: key == "Camera",
            "get": lambda self, key: owner,
        })()})()
        call = StepCall(node_id="combo", step_id="gui.object.action")
        self.assertEqual(_infer_condition(context, call, {
            "component_id": "Camera", "action": {"type": "select_item", "value": 2},
        }), {"object": "Camera", "property": "selected_index", "equals": 2})

    def test_menu_path_uses_scoped_bounds_for_real_click(self):
        endpoint = type("Endpoint", (), {
            "pid": 42,
            "request": lambda self, operation, **kwargs: {
                "path": [{"id": "open"}], "terminal_bounds": [100, 200, 80, 20],
            },
        })()
        with patch.object(JavaFxBridgeDriver, "_find_unique", return_value=(endpoint, {}, ())), \
             patch("automation_harness.core.pointer_actions.click_bounds", return_value={"x": 140, "y": 210}) as click:
            result = JavaFxBridgeDriver().select_menu_path([{"criteria": {"id": "open"}}])
        click.assert_called_once_with([100, 200, 80, 20])
        self.assertEqual(result["pointer"], {"x": 140, "y": 210})

    def test_menu_refuses_to_click_without_rendered_bounds(self):
        endpoint = type("Endpoint", (), {
            "pid": 42,
            "request": lambda self, operation, **kwargs: {"path": [{"id": "open"}]},
        })()
        with patch.object(JavaFxBridgeDriver, "_find_unique", return_value=(endpoint, {}, ())), \
             patch("automation_harness.core.pointer_actions.click_bounds") as click:
            with self.assertRaisesRegex(RuntimeError, "owner-scoped rendered bounds"):
                JavaFxBridgeDriver().select_menu_path([{"criteria": {"id": "open"}}])
        click.assert_not_called()


if __name__ == "__main__":
    unittest.main()
