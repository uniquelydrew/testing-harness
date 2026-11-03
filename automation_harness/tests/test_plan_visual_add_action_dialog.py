"""Interactive GTK regression for dynamic Test Plan Add Object Action inputs.

Requires a display (e.g. the RHEL desktop or xvfb-run); the headless CI
environment skips the GUI driver without suppressing pure catalog tests.
"""
import json

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.authoring.gui.plan_visual_window import VisualTestPlanWindow
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType
from automation_harness.models.plan import TestPlan


@pytest.fixture
def editor():
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display unavailable")
    tabs = ComponentDefinition(
        "Advanced Details Window.Tabs",
        object_type=ObjectType.TAB_CONTAINER,
        framework="javafx",
        actions=frozenset({"focus", "select_item"}),
        strategies=(ComponentStrategy("javafx", {"identification": {
            "mandatory": {"class": "edu.mit.ll.ersa.common.dashboard.components.AdvancedDetailsDisplay"}
        }}),),
        properties={"tabs": [
            {"index": 0, "id": None, "text": "Camera Status"},
            {"index": 3, "id": "sessionViewTab", "text": "Active Sessions"},
        ]},
    )
    text_field = ComponentDefinition(
        "Advanced Details Window.Search",
        object_type=ObjectType.TEXT_FIELD,
        framework="javafx",
        actions=frozenset({"set_text"}),
    )
    subject = VisualTestPlanWindow.__new__(VisualTestPlanWindow)
    subject.window = Gtk.Window()
    subject.repository = ComponentRepository({
        tabs.component_id: tabs, text_field.component_id: text_field,
    })
    subject.project = None
    subject.plan = TestPlan("action-dialog")
    subject.flow_tree = None
    subject.selected = lambda *_args: None
    subject.refresh_objects = lambda: None
    subject.refresh_all = lambda: None
    subject.mark_dirty = lambda *_args: None
    subject.set_status = lambda *_args: None
    subject.error = lambda _title, error: pytest.fail(error)
    yield subject, tabs, text_field
    subject.window.destroy()


def _widgets(parent):
    yield parent
    if isinstance(parent, Gtk.Container):
        for child in parent.get_children():
            yield from _widgets(child)


def _run_editor_action(editor, object_id, action_id, probe):
    errors = []
    timed_out = [False]

    def close_if_hung():
        for window in Gtk.Window.list_toplevels():
            if isinstance(window, Gtk.Dialog) and window.get_title() == "Add Object Action":
                timed_out[0] = True
                window.response(Gtk.ResponseType.CANCEL)
        return False

    def inspect_dialog():
        dialogs = [window for window in Gtk.Window.list_toplevels()
                   if isinstance(window, Gtk.Dialog)
                   and window.get_title() == "Add Object Action"]
        if not dialogs:
            errors.append(AssertionError("Add Object Action dialog did not open"))
            return False
        dialog = dialogs[-1]
        try:
            controls = list(_widgets(dialog))
            combos = [item for item in controls if isinstance(item, Gtk.ComboBoxText)]
            object_combo = next(item for item in combos if item.get_active_id() == object_id)
            action_combo = next(item for item in combos if item.get_active_id() == action_id)
            probe(dialog, object_combo, action_combo)
        except Exception as exc:
            errors.append(exc)
            dialog.response(Gtk.ResponseType.CANCEL)
        return False

    # Keep the main-loop integration test bounded if an assertion fails
    # inside a GTK event callback (which GTK would otherwise only log).
    source = GLib.timeout_add_seconds(4, close_if_hung)
    GLib.idle_add(inspect_dialog)
    try:
        editor.add_object_action(component_id=object_id, action_id=action_id)
    finally:
        GLib.source_remove(source)
    assert not timed_out[0], "Add Object Action dialog did not return"
    assert not errors, errors


def test_select_item_inputs_rebuild_and_emit_owner_scoped_tab_action(editor):
    subject, tabs, _field = editor

    def probe(dialog, _object_combo, action_combo):
        action_combo.set_active_id("focus")
        assert not any(isinstance(item, Gtk.ComboBoxText) and item.get_has_entry()
                       and item.get_visible() for item in _widgets(dialog))
        action_combo.set_active_id("select_item")
        selectors = [
            item for item in _widgets(dialog)
            if isinstance(item, Gtk.ComboBoxText)
            and item.get_has_entry() and item.get_visible()
        ]
        assert len(selectors) == 1
        encoded = json.dumps({
            "kind": "tab",
            "criteria": {"id": "sessionViewTab", "text": "Active Sessions", "index": 3},
        }, separators=(",", ":"), sort_keys=True)
        assert selectors[0].set_active_id(encoded)
        dialog.response(Gtk.ResponseType.OK)

    _run_editor_action(subject, tabs.component_id, "select_item", probe)
    assert len(subject.plan.steps) == 1
    call = subject.plan.steps[0]
    assert call.inputs["component_id"] == tabs.object_id
    assert call.inputs["action"] == {
        "type": "select_item",
        "selector": {
            "kind": "tab",
            "criteria": {"id": "sessionViewTab", "text": "Active Sessions", "index": 3},
        },
    }


def test_switching_objects_updates_typed_parameter_editor(editor):
    subject, tabs, field = editor

    def probe(dialog, object_combo, action_combo):
        object_combo.set_active_id(field.component_id)
        assert action_combo.set_active_id("set_text")
        labels = [
            item.get_text() for item in _widgets(dialog)
            if isinstance(item, Gtk.Label)
        ]
        assert "value *" in labels
        entries = [
            item for item in _widgets(dialog)
            if isinstance(item, Gtk.Entry) and item.get_placeholder_text()
            == "Text value supplied to the object action."
        ]
        assert len(entries) == 1
        entries[0].set_text("ABC")
        dialog.response(Gtk.ResponseType.OK)

    _run_editor_action(subject, tabs.component_id, "select_item", probe)
    assert len(subject.plan.steps) == 1
    assert subject.plan.steps[0].inputs == {
        "component_id": field.object_id,
        "action": {"type": "set_text", "value": "ABC"},
    }


def test_switching_visual_assertion_back_to_select_item_recreates_selector(editor):
    subject, tabs, _field = editor

    def probe(dialog, _object_combo, action_combo):
        assert action_combo.set_active_id("assert_match")
        assert any(
            isinstance(item, Gtk.Button) and item.get_label() == "Add New Match Image…"
            and item.get_visible()
            for item in _widgets(dialog)
        )
        assert action_combo.set_active_id("select_item")
        selectors = [
            item for item in _widgets(dialog)
            if isinstance(item, Gtk.ComboBoxText)
            and item.get_has_entry() and item.get_visible()
        ]
        assert len(selectors) == 1
        encoded = json.dumps({
            "kind": "tab", "criteria": {"text": "Camera Status", "index": 0}
        }, separators=(",", ":"), sort_keys=True)
        assert selectors[0].set_active_id(encoded)
        dialog.response(Gtk.ResponseType.OK)

    _run_editor_action(subject, tabs.component_id, "select_item", probe)
    assert subject.plan.steps[0].inputs["action"]["selector"]["criteria"]["text"] == "Camera Status"
