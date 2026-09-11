"""Visual assertion steps used by artifact-routed authoring."""
from __future__ import annotations

from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext
from automation_harness.drivers.vision_driver import VisionDriver
from automation_harness.core.visual_baselines import VisualBaselineError


@step(
    "gui.object.visual.assert",
    domain="gui",
    description="Assert that a logical GUI object's current pixels match an approved repository image.",
    capabilities={"components", "screen-capture"},
    outputs={
        "comparison": "$",
        "source": "source",
        "capture": "capture",
        "diff": "diff",
        "match_percentage": "match_percentage",
    },
)
def gui_object_visual_assert(
    ctx: TestContext,
    component_id: str,
    variant_key: str,
    minimum_match_percentage: float | None = None,
):
    handle = ctx.component(component_id)
    resolved = handle.resolve()
    bounds = resolved.metadata.get("bounds")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise VisualBaselineError("component %r did not resolve desktop bounds for visual comparison" % component_id)
    bounds = tuple(int(value) for value in bounds)
    if bounds[2] <= 0 or bounds[3] <= 0:
        raise VisualBaselineError("component visual bounds must be positive")
    visual = handle.definition.visual or {}
    variants = visual.get("variants", {})
    if variant_key not in variants:
        raise VisualBaselineError("component %r has no visual match image %r" % (component_id, variant_key))
    variant = variants[variant_key]
    source = handle.definition.repository_path
    if source is None:
        raise VisualBaselineError("component visual match has no repository source path")
    root = source.parent.resolve()
    baseline = (root / variant["image"]).resolve()
    mask = (root / variant["mask"]).resolve() if variant.get("mask") else None
    visual_root = (root / "visual").resolve()
    if visual_root not in baseline.parents or (mask is not None and visual_root not in mask.parents):
        raise VisualBaselineError("component visual match escapes repository visual directory")

    pixel_tolerance = int(variant.get("pixel_tolerance", 12))
    legacy_difference_ratio = float(variant.get("max_difference_ratio", 0.01))
    if minimum_match_percentage is None:
        # Backward compatibility for plans authored before the threshold became
        # an explicit assertion input.
        required_match_percentage = (1.0 - legacy_difference_ratio) * 100.0
    else:
        if isinstance(minimum_match_percentage, bool) or not isinstance(minimum_match_percentage, (int, float)):
            raise ValueError("minimum_match_percentage must be a number from 0 through 100")
        required_match_percentage = float(minimum_match_percentage)
    if required_match_percentage < 0.0 or required_match_percentage > 100.0:
        raise ValueError("minimum_match_percentage must be between 0 and 100")

    # Measure first without failing so the run always retains source/capture/diff
    # artifacts and an explicit percentage, including failed assertions.
    comparison = VisionDriver(ctx).compare_baseline(
        baseline,
        bounds=bounds,
        mask=mask,
        pixel_tolerance=pixel_tolerance,
        max_difference_ratio=1.0,
        name="%s-%s-match" % (component_id, variant_key),
    )
    match_percentage = max(0.0, min(100.0, (1.0 - comparison.difference_ratio) * 100.0))
    maximum_difference_ratio = 1.0 - (required_match_percentage / 100.0)
    passed = match_percentage >= required_match_percentage
    result = {
        "component_id": component_id,
        "variant_key": variant_key,
        "source": str(baseline),
        "capture": str(comparison.actual.relative_to(ctx.run_dir)),
        "expected": str(comparison.expected.relative_to(ctx.run_dir)),
        "diff": str(comparison.diff.relative_to(ctx.run_dir)),
        "changed_pixels": comparison.changed_pixels,
        "compared_pixels": comparison.compared_pixels,
        "difference_ratio": comparison.difference_ratio,
        "match_percentage": match_percentage,
        "minimum_match_percentage": required_match_percentage,
        "pixel_tolerance": pixel_tolerance,
        "maximum_difference_ratio": maximum_difference_ratio,
        "passed": passed,
    }
    ctx.evidence.record(
        "component_visual_match_asserted",
        component_id=component_id,
        variant_key=variant_key,
        visual_revision=visual.get("revision", 0),
        source=result["source"],
        capture=result["capture"],
        expected=result["expected"],
        diff=result["diff"],
        changed_pixels=result["changed_pixels"],
        compared_pixels=result["compared_pixels"],
        difference_ratio=result["difference_ratio"],
        match_percentage=result["match_percentage"],
        minimum_match_percentage=required_match_percentage,
        maximum_difference_ratio=maximum_difference_ratio,
        passed=passed,
    )
    if not passed:
        raise AssertionError(
            "visual match %.2f%% is below required %.2f%%; source=%s; capture=%s; diff=%s"
            % (
                match_percentage,
                required_match_percentage,
                result["source"],
                result["capture"],
                result["diff"],
            )
        )
    return result
