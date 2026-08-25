from pathlib import Path

from automation_harness.core.driver_manager import DriverManager
from automation_harness.steps.navigation_steps import activate_component


def test_follow_control_and_map_overlay(ctx):
    """Template: keep the interaction semantic and validate the visual result externally."""
    activate_component(ctx, "java.swing.follow")
    canvas = ctx.component("java.fx.map_canvas").resolve()
    bounds = tuple(int(value) for value in canvas.metadata["bounds"])
    vision = DriverManager.from_context(ctx).vision
    vision.wait_for_color((30, 144, 255), bounds=bounds, name="follow-overlay")

    # Replace with an approved image committed alongside this bundle. A black
    # mask pixel excludes volatile content such as timestamps or live imagery.
    baseline = Path(__file__).with_name("map-stable-baseline.png")
    mask = Path(__file__).with_name("map-stable-mask.png")
    vision.compare_baseline(baseline, bounds=bounds, mask=mask, name="map-stable-layout")
