from __future__ import annotations

from typing import Any

from automation_harness.core.driver_manager import DriverManager
from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext


@step(
    "track.create_moving",
    domain="track",
    description="Create a synthetic moving track with a stable logical track ID.",
    capabilities={"tracking"},
    risk="synthetic_control",
    aliases=("create_moving_track",),
    outputs={"track": "$", "track_id": "track_id", "x": "x", "y": "y", "vx": "vx", "vy": "vy", "visible": "visible", "followed": "followed"},
)
def create_moving_track(
    ctx: TestContext,
    track_id: str = "reference-track",
    *,
    x: float = 10.0,
    y: float = 10.0,
    vx: float = 5.0,
    vy: float = 0.0,
) -> dict[str, Any]:
    return DriverManager.from_context(ctx).tracking.create_track(track_id, x=x, y=y, vx=vx, vy=vy)


@step(
    "track.follow",
    domain="track",
    description="Mark a track as the currently followed track.",
    capabilities={"tracking"},
    risk="synthetic_control",
    aliases=("follow_track",),
    outputs={"track": "$", "track_id": "track_id", "followed": "followed"},
)
def follow_track(ctx: TestContext, track_id: str) -> dict[str, Any]:
    return DriverManager.from_context(ctx).tracking.follow(track_id)


@step(
    "track.wait_for_motion",
    domain="track",
    description="Wait until a track moves away from a supplied initial X position.",
    capabilities={"tracking"},
    aliases=("wait_for_motion",),
    outputs={"track": "$", "track_id": "track_id", "x": "x", "y": "y"},
)
def wait_for_motion(ctx: TestContext, track_id: str, *, initial_x: float) -> dict[str, Any]:
    return DriverManager.from_context(ctx).tracking.wait_until_moved(track_id, initial_x=initial_x)


@step(
    "track.create_and_follow",
    domain="track",
    description="Composite reusable action that creates a moving track and begins following it.",
    capabilities={"tracking"},
    risk="synthetic_control",
    aliases=("create_and_follow_track",),
    outputs={"result": "$", "created": "created", "followed": "followed", "track_id": "created.track_id"},
)
def create_and_follow_track(
    ctx: TestContext,
    track_id: str = "reference-track",
    *,
    x: float = 10.0,
    y: float = 10.0,
    vx: float = 5.0,
    vy: float = 0.0,
) -> dict[str, Any]:
    created = ctx.run_step("track.create_moving", track_id, x=x, y=y, vx=vx, vy=vy)
    followed = ctx.run_step("track.follow", track_id)
    return {"created": created, "followed": followed}
