from automation_harness.core.driver_manager import DriverManager
from automation_harness.steps.navigation_steps import activate_component


def test_follow_control_and_map_overlay(ctx):
    """Template: keep the interaction semantic and validate the visual result externally."""
    activate_component(ctx, "java.swing.follow")
    canvas = ctx.component("java.fx.map_canvas").resolve()
    bounds = tuple(int(value) for value in canvas.metadata["bounds"])
    vision = DriverManager.from_context(ctx).vision
    vision.wait_for_color((30, 144, 255), bounds=bounds, name="follow-overlay")

    # Visual golds are approved component metadata, stored under the repository's
    # visual/ directory and selected by the exact host visual profile.
    ctx.component("java.fx.map_canvas").assert_visual()
