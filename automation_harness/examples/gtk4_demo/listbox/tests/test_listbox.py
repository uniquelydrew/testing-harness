import pytest
from automation_harness.steps.navigation_steps import get_component_selection, select_component_child


def test_list_selection_is_observable(ctx):
    pytest.importorskip("pyatspi")
    select_component_child(ctx, "gtk_demo.listbox.items", 0)
    assert get_component_selection(ctx, "gtk_demo.listbox.items")
