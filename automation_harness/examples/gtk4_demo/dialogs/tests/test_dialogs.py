import pytest
from automation_harness.steps.navigation_steps import activate_component


def test_dialog_launcher_exposes_a_real_action(ctx):
    pytest.importorskip("pyatspi")
    assert activate_component(ctx, "gtk_demo.dialogs.open")["action"]
