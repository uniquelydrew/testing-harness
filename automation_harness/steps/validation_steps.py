from __future__ import annotations

from collections.abc import Sequence

from automation_harness.core.driver_manager import DriverManager
from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext


def _record_assertion(ctx: TestContext, name: str, expected, actual, passed: bool, **fields) -> None:
    ctx.evidence.record(
        "assertion",
        assertion=name,
        expected=expected,
        actual=actual,
        passed=passed,
        **fields,
    )


@step(
    "validation.track.followed",
    domain="validation",
    description="Assert that a track is currently marked as followed.",
    capabilities={"tracking"},
    aliases=("assert_track_followed",),
)
def assert_track_followed(ctx: TestContext, track_id: str) -> None:
    track = DriverManager.from_context(ctx).tracking.get_track(track_id)
    passed = bool(track["followed"])
    _record_assertion(ctx, "track_followed", True, passed, passed, track_id=track_id)
    assert passed, f"expected track {track_id!r} to be followed; actual={track}"


@step(
    "validation.track.visibility",
    domain="validation",
    description="Assert a track's current visibility state.",
    capabilities={"tracking"},
    aliases=("assert_track_visibility",),
)
def assert_track_visibility(ctx: TestContext, track_id: str, expected: bool) -> None:
    track = DriverManager.from_context(ctx).tracking.get_track(track_id)
    actual = bool(track["visible"])
    passed = actual is expected
    _record_assertion(ctx, "track_visibility", expected, actual, passed, track_id=track_id)
    assert passed, f"expected track {track_id!r} visibility={expected}; actual={track}"


@step(
    "validation.equal",
    domain="validation",
    description="Assert equality while recording expected and actual values as structured evidence.",
    aliases=("assert_equal",),
)
def assert_equal(ctx: TestContext, name: str, actual, expected) -> None:
    passed = actual == expected
    _record_assertion(ctx, name, expected, actual, passed)
    assert passed, f"{name}: expected {expected!r}, got {actual!r}"


@step(
    "validation.mosaic.tiles",
    domain="validation",
    description="Assert the ordered contents of the current mosaic tile collection.",
    capabilities={"mosaic"},
    aliases=("assert_tiles",),
)
def assert_tiles(ctx: TestContext, actual: Sequence[str], expected: Sequence[str]) -> None:
    actual_list = list(actual)
    expected_list = list(expected)
    passed = actual_list == expected_list
    _record_assertion(ctx, "mosaic_tiles", expected_list, actual_list, passed)
    assert passed, f"mosaic_tiles: expected {expected_list!r}, got {actual_list!r}"
