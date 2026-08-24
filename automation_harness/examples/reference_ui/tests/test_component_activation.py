import pytest

from automation_harness.steps.navigation_steps import activate_component, resolve_component
from automation_harness.steps.validation_steps import assert_equal


def test_logical_component_activation_uses_real_atspi_path(ctx):
    pytest.importorskip("pyatspi", reason="AT-SPI integration requires the system pyatspi binding")

    resolved = resolve_component(ctx, "reference.threat.medium")
    assert resolved.strategy == "atspi"

    result = activate_component(ctx, "reference.threat.medium")
    assert result["action"].lower() in {"click", "press", "activate"}

    actual = ctx.require_reference().request("state")["threat_level"]
    assert_equal(ctx, "threat_state_after_atspi_activation", actual, "MEDIUM")
