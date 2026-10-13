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


def test_menu_route_can_be_typed_without_inventory():
    picker = MenuPathPicker({})
    picker._entry.set_text("File > Open")
    assert picker.selected_value() == ("File > Open", True)


def test_menu_route_edit_overrides_prior_selected_option():
    picker = MenuPathPicker({"open": {"label": "Open", "kind": "menu_item"}})
    picker.set_selected(["open"])
    picker._entry.set_text("File > Save As")
    assert picker.selected_value() == ("File > Save As", True)


def test_menu_route_is_restored_without_inventory():
    picker = MenuPathPicker({})
    picker.set_selected("File > Open")
    assert picker.selected_value() == ("File > Open", True)


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
