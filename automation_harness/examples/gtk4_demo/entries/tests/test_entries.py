import pytest
from automation_harness.steps.navigation_steps import get_component_text, set_component_text


def test_editable_text_round_trips_through_atspi(ctx):
    pytest.importorskip("pyatspi")
    set_component_text(ctx, "gtk_demo.entries.text", "automation harness")
    assert get_component_text(ctx, "gtk_demo.entries.text") == "automation harness"
