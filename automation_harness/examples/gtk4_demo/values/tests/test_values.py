import pytest
from automation_harness.steps.navigation_steps import get_component_value, set_component_value


def test_numeric_value_is_set_and_observed(ctx):
    pytest.importorskip("pyatspi")
    set_component_value(ctx, "gtk_demo.values.spin", 7)
    assert get_component_value(ctx, "gtk_demo.values.spin") == 7
