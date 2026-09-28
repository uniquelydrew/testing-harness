"""Small executable contracts for menu completion and combo ownership."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from automation_harness.core.completion import _infer_condition
from automation_harness.core.interaction_preparation import preparation_requirement
from automation_harness.drivers.javafx_bridge import (
    JavaFxBridgeDriver, JavaFxBridgeProtocolError, _captured_recording_node,
)
from automation_harness.models.component import ComponentDefinition
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.models.plan import StepCall
from automation_harness.recording import RecordingSession
from automation_harness.recording.observations import PointerInteraction


class MenuComboRegression(unittest.TestCase):
    def test_menu_selection_raises_owner_window_without_focusing_popup(self):
        requirement = preparation_requirement(ActionType.SELECT_MENU_ITEM)
        self.assertTrue(requirement.activate_window)
        self.assertFalse(requirement.request_focus)

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

    def test_click_completion_does_not_recheck_dialog_button_that_disappears(self):
        button = ComponentDefinition(
            component_id="OK Button", object_type=ObjectType.BUTTON,
            expected_states={"enabled": True, "showing": True, "visible": True},
        )
        context = type("Context", (), {"components": type("Components", (), {
            "contains": lambda self, key: key == "OK Button",
            "get": lambda self, key: button,
        })()})()
        call = StepCall(node_id="step-001", step_id="gui.object.action")
        self.assertIsNone(_infer_condition(context, call, {
            "component_id": "OK Button", "action": {"type": "click"},
        }))
        self.assertEqual(_infer_condition(context, replace(call, completion={
            "mode": "automatic", "effects": [{"object": "OK Button", "transition": "becomes-absent"}],
        }), {"component_id": "OK Button", "action": {"type": "click"}}),
            {"object": "OK Button", "state": "absent", "equals": True})

    def test_combo_popup_cell_is_owned_by_combo(self):
        owner = {"name": "Camera", "role": "combo box", "class": "javafx.scene.control.ComboBox",
                 "id": "camera", "window": "Main", "bounds": [10, 10, 100, 25]}
        cell = {"name": "Decorative cell", "role": "list item", "class": "javafx.scene.control.ListCell",
                "window": "Popup", "combo_selection": {"index": 2, "text": "North", "owner": owner}}
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
        self.assertEqual(interactions[0].parameters, {"value": "North"})

    def test_combo_popup_miss_consumes_the_second_click(self):
        owner = _captured_recording_node({
            "name": "Camera", "role": "combo box", "class": "javafx.scene.control.ComboBox",
            "id": "camera", "window": "Main", "bounds": [10, 10, 100, 25],
        })
        covered_button = _captured_recording_node({
            "name": "Save", "role": "button", "class": "javafx.scene.control.Button",
            "id": "save", "window": "Main", "bounds": [10, 40, 100, 25],
        })
        session = RecordingSession()
        session.start()
        session.observe(PointerInteraction(0.5, "javafx", owner, {}, "primary", "released", (20, 20)))
        # A transient-popup hit-test failure used to create a spurious Save
        # click here.  It is the unresolved second half of the combo gesture.
        session.observe(PointerInteraction(1.0, "javafx", covered_button, {}, "primary", "released", (20, 50)))
        self.assertEqual(session.stop(), ())

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
        requests = []
        def request(_self, operation, **kwargs):
            requests.append((operation, kwargs))
            if operation == "finish_menu_click":
                return {"pointer_action_observed": False, "fallback_fired": True}
            return {"path": [{"id": "open"}], "terminal_bounds": [100, 200, 80, 20],
                    "click_token": "scoped-menu-item"}
        endpoint = type("Endpoint", (), {"pid": 42, "request": request})()
        with patch.object(JavaFxBridgeDriver, "_find_unique", return_value=(endpoint, {}, ())), \
             patch("automation_harness.core.pointer_actions.click_bounds", return_value={"x": 140, "y": 210}) as click:
            result = JavaFxBridgeDriver().select_menu_path([{"criteria": {"id": "open"}}])
        click.assert_called_once_with([100, 200, 80, 20])
        self.assertEqual(result["pointer"], {"x": 140, "y": 210})
        self.assertEqual(requests[-1], ("finish_menu_click", {
            "timeout": 35.0, "click_token": "scoped-menu-item", "clicked": True,
        }))
        self.assertTrue(result["fallback_fired"])

    def test_failed_pointer_click_does_not_fire_menu_item(self):
        requests = []
        def request(_self, operation, **kwargs):
            requests.append((operation, kwargs))
            return ({"click_token": "item", "terminal_bounds": [1, 2, 3, 4]}
                    if operation == "select_menu_path" else {})
        endpoint = type("Endpoint", (), {"pid": 42, "request": request})()
        with patch.object(JavaFxBridgeDriver, "_find_unique", return_value=(endpoint, {}, ())), \
             patch("automation_harness.core.pointer_actions.click_bounds", side_effect=RuntimeError("pointer failed")):
            with self.assertRaisesRegex(RuntimeError, "pointer failed"):
                JavaFxBridgeDriver().select_menu_path([{"criteria": {"id": "open"}}])
        self.assertFalse(requests[-1][1]["clicked"])

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

    def test_missing_rendered_bounds_fires_only_resolved_menu_path(self):
        requests = []
        def request(_self, operation, **kwargs):
            requests.append((operation, kwargs))
            if operation == "menu_dispatch_status":
                return {"observed": True, "finished": False, "error": None}
            if kwargs["pointer_terminal"]:
                raise JavaFxBridgeProtocolError(
                    "IllegalStateException: selected menu item has no rendered screen bounds"
                )
            return {"path": [{"id": "cameraSelectorMenuItem"}], "dispatch_token": "camera-action"}
        endpoint = type("Endpoint", (), {"pid": 42, "request": request})()
        with patch.object(JavaFxBridgeDriver, "_find_unique", return_value=(endpoint, {}, ())), \
             patch("automation_harness.drivers.javafx_bridge.time.monotonic", side_effect=(0.0, 2.0, 2.0)), \
             patch("automation_harness.core.pointer_actions.click_bounds") as click:
            result = JavaFxBridgeDriver().select_menu_path([{"criteria": {"id": "cameraSelectorMenuItem"}}])
        self.assertEqual([args["pointer_terminal"] for operation, args in requests
                          if operation == "select_menu_path"], [True, False])
        self.assertEqual(requests[-1][0], "menu_dispatch_status")
        self.assertEqual(result["execution"], "owner_scoped_javafx_fire")
        self.assertIsNone(result["pointer"])
        click.assert_not_called()

    def test_menu_dispatch_does_not_pass_without_action_event(self):
        endpoint = type("Endpoint", (), {
            "request": lambda self, operation, **kwargs: {
                "observed": False, "finished": True, "error": "handler failed",
            },
        })()
        with self.assertRaisesRegex(JavaFxBridgeProtocolError, "handler failed"):
            JavaFxBridgeDriver._await_menu_dispatch(endpoint, "resolved-menu-item")


if __name__ == "__main__":
    unittest.main()
