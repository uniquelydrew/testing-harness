import pytest

from automation_harness.steps.camera_steps import get_track, set_track_visibility
from automation_harness.steps.track_steps import create_moving_track, wait_for_motion
from automation_harness.steps.validation_steps import assert_track_visibility


@pytest.mark.reference
@pytest.mark.tracking
@pytest.mark.integration
def test_synthetic_video_target_motion_and_visibility(ctx):
    initial = create_moving_track(ctx, "video-alpha", x=4.0, y=4.0, vx=12.0, vy=-2.0)
    moved = wait_for_motion(ctx, "video-alpha", initial_x=initial["x"])
    assert moved["x"] > initial["x"]

    set_track_visibility(ctx, "video-alpha", False)
    assert_track_visibility(ctx, "video-alpha", False)
    set_track_visibility(ctx, "video-alpha", True)
    assert_track_visibility(ctx, "video-alpha", True)
    assert get_track(ctx, "video-alpha")["track_id"] == "video-alpha"
