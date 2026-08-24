import pytest

from automation_harness.steps.camera_steps import triangulate
from automation_harness.steps.validation_steps import assert_equal


@pytest.mark.reference
@pytest.mark.integration
def test_synthetic_triangulation(ctx):
    result = triangulate(ctx, [(0.0, 0.0), (10.0, 0.0), (5.0, 15.0)])
    assert_equal(ctx, "triangulated_x", result["x"], 5.0)
    assert_equal(ctx, "triangulated_y", result["y"], 5.0)
    assert result["spread"] > 0.0
