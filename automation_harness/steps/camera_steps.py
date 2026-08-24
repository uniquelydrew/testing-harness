from __future__ import annotations

from typing import Sequence

from automation_harness.core.driver_manager import DriverManager
from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext


@step(
    "camera.track.set_visibility",
    domain="camera",
    description="Set whether a synthetic/reference track is visible in the video surface.",
    capabilities={"tracking"},
    risk="synthetic_control",
    aliases=("set_track_visibility",),
    outputs={"track": "$", "track_id": "track_id", "visible": "visible"},
)
def set_track_visibility(ctx: TestContext, track_id: str, visible: bool) -> dict:
    return DriverManager.from_context(ctx).tracking.set_visibility(track_id, visible)


@step(
    "camera.track.get",
    domain="camera",
    description="Read the current state of a track from the tracking adapter.",
    capabilities={"tracking"},
    aliases=("get_track",),
    outputs={"track": "$", "track_id": "track_id", "visible": "visible", "followed": "followed"},
)
def get_track(ctx: TestContext, track_id: str) -> dict:
    return DriverManager.from_context(ctx).tracking.get_track(track_id)


@step(
    "camera.triangulate",
    domain="camera",
    description="Triangulate a synthetic point from a sequence of observation coordinates.",
    capabilities={"triangulation"},
    aliases=("triangulate",),
    outputs={"result": "$", "x": "x", "y": "y", "spread": "spread"},
)
def triangulate(ctx: TestContext, points: Sequence[tuple[float, float]]) -> dict:
    return ctx.require_services().require_triangulation().triangulate(points)
