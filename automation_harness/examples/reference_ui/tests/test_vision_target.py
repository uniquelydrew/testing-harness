from automation_harness.core.driver_manager import DriverManager
from automation_harness.steps.track_steps import create_moving_track


def test_synthetic_track_is_visible_in_real_framebuffer(ctx):
    create_moving_track(ctx, "vision-track", x=50.0, y=50.0, vx=0.0, vy=0.0)

    canvas = ctx.component("reference.track.canvas").resolve()
    bounds = canvas.metadata["bounds"]
    assert bounds is not None

    match = DriverManager.from_context(ctx).vision.wait_for_color(
        (30, 144, 255),
        tolerance=8,
        bounds=tuple(int(value) for value in bounds),
        name="vision-track-detection",
    )
    assert match.width >= 8
    assert match.height >= 8
    assert match.confidence >= 0.95
