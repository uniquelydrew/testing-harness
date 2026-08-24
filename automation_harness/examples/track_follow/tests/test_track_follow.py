import pytest

from automation_harness.steps.track_steps import create_moving_track, follow_track, wait_for_motion
from automation_harness.steps.validation_steps import assert_track_followed


@pytest.mark.reference
@pytest.mark.tracking
@pytest.mark.integration
def test_track_follow(ctx):
    initial = create_moving_track(ctx, "alpha", x=10.0, y=20.0, vx=8.0, vy=1.0)
    moved = wait_for_motion(ctx, "alpha", initial_x=initial["x"])
    assert moved["x"] > initial["x"]

    follow_track(ctx, "alpha")
    assert_track_followed(ctx, "alpha")
