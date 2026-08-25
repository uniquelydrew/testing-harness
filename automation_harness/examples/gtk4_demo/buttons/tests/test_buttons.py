import pytest
from automation_harness.steps.navigation_steps import activate_component


def test_primary_button_is_resolvable_and_activatable(ctx):
    pytest.importorskip("pyatspi")
    result = activate_component(ctx, "gtk_demo.buttons.main")
    assert result["action"].casefold() in {"click", "press", "activate"}
