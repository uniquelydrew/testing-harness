import pytest

from automation_harness.steps.threat_steps import get_threat_level, set_threat_level
from automation_harness.steps.validation_steps import assert_equal


@pytest.mark.reference
@pytest.mark.integration
def test_threat_level_round_trip(ctx):
    assert_equal(ctx, "initial_threat_level", get_threat_level(ctx), "LOW")
    assert_equal(ctx, "set_medium_threat", set_threat_level(ctx, "MEDIUM"), "MEDIUM")
    assert_equal(ctx, "read_medium_threat", get_threat_level(ctx), "MEDIUM")
    assert_equal(ctx, "set_high_threat", set_threat_level(ctx, "HIGH"), "HIGH")
