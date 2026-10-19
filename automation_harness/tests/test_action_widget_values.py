"""Regression coverage for authoring values independent of captured options."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.action_catalog import ActionInput
from automation_harness.authoring.gui.action_widgets import (
    MenuPathPicker, create_action_input_widget, read_action_input,
)
from automation_harness.models.component import ComponentDefinition


@pytest.fixture(autouse=True)
def require_display():
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display unavailable")


def test_unknown_selector_can_be_typed_and_read():
    component = ComponentDefinition("choice", subobjects={
        "known": {"kind": "option", "selector": {"name": "Known"}}
    })
    item = ActionInput("selector", "object")
    widget = create_action_input_widget(item, component, current="Unknown")
    assert read_action_input(item, widget) == (True, "Unknown")


def test_unknown_structured_selector_round_trip():
    component = ComponentDefinition("choice", subobjects={})
    item = ActionInput("selector", "object")
    widget = create_action_input_widget(item, component, current={"name": "Dynamic"})
    assert read_action_input(item, widget) == (True, {"name": "Dynamic"})

def _menu_options():
    return {
        "file": {
            "kind": "menu", "display_name": "File",
            "criteria": {"text": "File"},
            "subobjects": {
                "open": {"kind": "menu_item", "criteria": {"text": "Open"}},
                "disabled": {
                    "kind": "menu_item", "criteria": {"text": "Disabled"},
                    "enabled": False,
                },
            },
        },
    }


def test_menu_route_can_be_typed_without_inventory():
    picker = MenuPathPicker({})
    picker._entry.set_text("File > Open")
    assert read_action_input(ActionInput("path", "menu_path"), picker) == (True, "File > Open")


def test_menu_route_is_restored_without_inventory():
    picker = MenuPathPicker({})
    picker.set_selected("File > Save As")
    assert read_action_input(ActionInput("path", "menu_path"), picker) == (True, "File > Save As")


def test_captured_menu_path_round_trips_as_readable_text():
    picker = MenuPathPicker(_menu_options())
    picker.set_selected(["file", "open"])
    assert read_action_input(ActionInput("path", "menu_path"), picker) == (True, "File > Open")


def test_typed_menu_route_supersedes_prior_tree_choice():
    picker = MenuPathPicker(_menu_options())
    picker.set_selected(["file", "open"])
    picker._entry.set_text("File > Save As")
    assert read_action_input(ActionInput("path", "menu_path"), picker) == (True, "File > Save As")


def test_disabled_menu_option_cannot_be_committed_by_typing():
    picker = MenuPathPicker(_menu_options())
    picker._entry.set_text("File > Disabled")
    with pytest.raises(ValueError, match="disabled"):
        read_action_input(ActionInput("path", "menu_path"), picker)


def test_disabled_menu_option_cannot_be_committed_by_tree_selection():
    picker = MenuPathPicker(_menu_options())
    picker.set_selected(["file", "disabled"])
    with pytest.raises(ValueError, match="disabled"):
        read_action_input(ActionInput("path", "menu_path"), picker)


def test_empty_menu_route_is_not_a_present_value():
    picker = MenuPathPicker({})
    assert read_action_input(ActionInput("path", "menu_path"), picker) == (False, None)
