import pytest
from automation_harness.steps.navigation_steps import activate_component


def test_popover_trigger_is_activatable(ctx):
    pytest.importorskip("pyatspi")
    assert activate_component(ctx, "gtk_demo.popover.menu")["action"]
