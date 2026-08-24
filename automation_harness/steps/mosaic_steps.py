from __future__ import annotations

from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext


@step(
    "mosaic.tile.add",
    domain="mosaic",
    description="Add a source tile to the current mosaic, preserving idempotency.",
    capabilities={"mosaic"},
    risk="synthetic_control",
    aliases=("add_tile",),
    outputs={"tiles": "$"},
)
def add_tile(ctx: TestContext, tile: str) -> list[str]:
    return ctx.require_services().require_mosaic().add_tile(tile)


@step(
    "mosaic.tile.remove",
    domain="mosaic",
    description="Remove a source tile from the current mosaic.",
    capabilities={"mosaic"},
    risk="synthetic_control",
    aliases=("remove_tile",),
    outputs={"tiles": "$"},
)
def remove_tile(ctx: TestContext, tile: str) -> list[str]:
    return ctx.require_services().require_mosaic().remove_tile(tile)


@step(
    "mosaic.tiles.get",
    domain="mosaic",
    description="Return the ordered source tiles in the current mosaic.",
    capabilities={"mosaic"},
    aliases=("current_tiles",),
    outputs={"tiles": "$"},
)
def current_tiles(ctx: TestContext) -> list[str]:
    return ctx.require_services().require_mosaic().get_tiles()
