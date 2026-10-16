"""Regression coverage for authoring values independent of captured options."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.action_catalog import ActionInput
from automation_harness.authoring.gui.action_widgets import (
    create_action_input_widget, read_action_input,
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
