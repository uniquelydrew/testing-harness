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
from automation_harness.recording.observations import StateChanged


def _popup_owner(name, native_class, accessible_id):
    return {
        "name": name,
        "role": "combo box",
        "class": native_class,
        "id": accessible_id,
        "window": "Main",
        "bounds": [10, 10, 100, 25],
    }


def _popup_selection(owner, *, native_class, role, index, value):
    return _captured_recording_node({
        "name": value,
        "role": role,
        "class": native_class,
        "window": "Popup",
        "popup_selection": {
            "index": index,
            "value": value,
            "family": "popup_selector",
            "owner": owner,
        },
    })


def _record_popup_selection(selected):
    session = RecordingSession()
    session.start()
    opener = replace(selected, backend_properties={
        key: value for key, value in selected.backend_properties.items()
        if key != "popup_selection"
    })
    session.observe(PointerInteraction(0.5, "javafx", opener, {}, "primary", "released", (20, 20)))
    session.observe(PointerInteraction(1.0, "javafx", selected, {}, "primary", "released", (20, 20)))
    return session.stop()


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

    def test_legacy_combo_selection_payload_normalizes_to_popup_selection(self):
        owner = _popup_owner("Camera", "javafx.scene.control.ComboBox", "camera")
        cell = {"name": "Decorative cell", "role": "list item", "class": "javafx.scene.control.ListCell",
                 "window": "Popup", "combo_selection": {"index": 2, "text": "North", "owner": owner}}
        capture = _captured_recording_node(cell)
        self.assertEqual(capture.semantic_type(), ObjectType.COMBO_BOX)
        self.assertEqual(capture.candidate_strategy().type, "javafx")
        self.assertEqual(capture.backend_properties["popup_selection"]["index"], 2)

    def test_popup_selector_nodes_are_owned_by_their_selector(self):
        cases = (
            ("Camera", "javafx.scene.control.ComboBox", "camera", "javafx.scene.control.ListCell", "list item"),
            ("Site", "javafx.scene.control.ChoiceBox", "siteSelector_", "javafx.scene.control.MenuItem", "menu item"),
        )
        for name, owner_class, owner_id, popup_class, popup_role in cases:
            with self.subTest(owner_class=owner_class, popup_class=popup_class):
                selected = _popup_selection(
                    _popup_owner(name, owner_class, owner_id), native_class=popup_class,
                    role=popup_role, index=1, value="North",
                )
                self.assertEqual(selected.semantic_type(), ObjectType.COMBO_BOX)
                interactions = _record_popup_selection(selected)
                self.assertEqual([(item.action, item.target.accessible_id, item.parameters) for item in interactions], [
                    (ActionType.SELECT_ITEM, owner_id, {"value": "North"}),
                ])

    def test_choicebox_duplicate_selection_events_commit_once(self):
        owner = _captured_recording_node(_popup_owner(
            "Site", "javafx.scene.control.ChoiceBox", "siteSelector_"))
        selected = replace(owner, backend_properties={
            **owner.backend_properties,
            "popup_selection": {"family": "popup_selector", "value": "VC-A_elrti", "index": 1},
        })
        session = RecordingSession()
        session.start()
        session.observe(PointerInteraction(0.5, "javafx", owner, {}, "primary", "released", (20, 20)))
        session.observe(PointerInteraction(1.0, "javafx", selected, {}, "primary", "released", (20, 45)))
        session.observe(StateChanged(1.1, "javafx", owner, {}, "selected_item", None, "VC-A_elrti"))
        # AT-SPI may deliver a delayed release after JavaFX already committed.
        session.observe(PointerInteraction(3.1, "javafx", selected, {}, "primary", "released", (20, 45)))
        interactions = session.stop()
        self.assertEqual([(item.action, item.target.accessible_id, item.parameters)
                          for item in interactions], [
            (ActionType.SELECT_ITEM, "siteSelector_", {"value": "VC-A_elrti"}),
        ])

    def test_choicebox_state_change_before_popup_pointer_commits_once(self):
        owner = _captured_recording_node(_popup_owner(
            "Site", "javafx.scene.control.ChoiceBox", "siteSelector_"))
        selected = replace(owner, backend_properties={
            **owner.backend_properties,
            "popup_selection": {"family": "popup_selector", "value": "North", "index": 1},
        })
        session = RecordingSession()
        session.start()
        session.observe(PointerInteraction(0.5, "javafx", owner, {}, "primary", "released", (20, 20)))
        session.observe(StateChanged(0.8, "javafx", owner, {}, "value", None, "North"))
        session.observe(PointerInteraction(1.0, "javafx", selected, {}, "primary", "released", (20, 45)))
        self.assertEqual([(item.action, item.parameters) for item in session.stop()], [
            (ActionType.SELECT_ITEM, {"value": "North"}),
        ])

    def test_combo_popup_miss_consumes_the_second_click(self):
        owner = _captured_recording_node(_popup_owner("Camera", "javafx.scene.control.ComboBox", "camera"))
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

    def test_popup_selection_miss_cannot_author_covered_control(self):
        owner = _captured_recording_node(_popup_owner("Site", "javafx.scene.control.ChoiceBox", "siteSelector_"))
        covered = _captured_recording_node({
            "name": "Follow", "role": "button", "class": "javafx.scene.control.Button",
            "id": "followButton_", "window": "Main", "bounds": [10, 40, 100, 25],
        })
        session = RecordingSession()
        session.start()
        session.observe(PointerInteraction(0.5, "javafx", owner, {}, "primary", "released", (20, 20)))
        session.observe(PointerInteraction(1.0, "javafx", covered, {}, "primary", "released", (20, 50)))
        self.assertEqual(session.stop(), ())

    def test_popup_owner_value_change_is_authoritative_when_popup_node_is_unavailable(self):
        owner = _captured_recording_node({
            "name": "Date", "role": "date picker", "class": "javafx.scene.control.DatePicker",
            "id": "dateSelector", "window": "Main", "bounds": [10, 10, 100, 25],
        })
        session = RecordingSession()
        session.start()
        session.observe(PointerInteraction(0.5, "javafx", owner, {}, "primary", "released", (20, 20)))
        session.observe(StateChanged(1.0, "javafx", owner, {}, "value", None, "2026-09-28"))
        interactions = session.stop()
        self.assertEqual([(item.action, item.target.accessible_id, item.parameters) for item in interactions], [
            (ActionType.SELECT_ITEM, "dateSelector", {"value": "2026-09-28"}),
        ])

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
