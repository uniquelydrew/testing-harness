import pytest

from automation_harness.steps.mosaic_steps import add_tile, current_tiles, remove_tile
from automation_harness.steps.validation_steps import assert_tiles


@pytest.mark.reference
@pytest.mark.integration
def test_mosaic_tile_lifecycle(ctx):
    assert_tiles(ctx, add_tile(ctx, "camera-a"), ["camera-a"])
    assert_tiles(ctx, add_tile(ctx, "camera-b"), ["camera-a", "camera-b"])
    assert_tiles(ctx, add_tile(ctx, "camera-a"), ["camera-a", "camera-b"])
    assert_tiles(ctx, remove_tile(ctx, "camera-a"), ["camera-b"])
    assert_tiles(ctx, current_tiles(ctx), ["camera-b"])
