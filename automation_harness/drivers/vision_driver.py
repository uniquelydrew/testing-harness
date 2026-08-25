from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from automation_harness.core.test_context import TestContext
from automation_harness.core.visual_baselines import VisualBaselineError, VisualProfile, select_visual_variant


class VisionUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class VisionMatch:
    x: int
    y: int
    width: int
    height: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.width // 2, self.y + self.height // 2)


@dataclass(frozen=True)
class BaselineComparison:
    changed_pixels: int
    compared_pixels: int
    difference_ratio: float
    actual: Path
    expected: Path
    diff: Path


@dataclass
class VisionDriver:
    context: TestContext

    def capture(self, *, name: str = "screen") -> Path:
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise VisionUnavailable("Pillow is required for screen capture") from exc
        screenshots = self.context.run_dir / "screenshots"
        screenshots.mkdir(parents=True, exist_ok=True)
        path = _unique_path(screenshots, name, ".png")
        try:
            image = ImageGrab.grab()
        except Exception as exc:
            raise VisionUnavailable(f"screen capture failed: {exc}") from exc
        image.save(path)
        self.context.evidence.record(
            "screenshot_captured",
            path=str(path.relative_to(self.context.run_dir)),
            width=image.width,
            height=image.height,
        )
        return path

    def capture_region(self, bounds: tuple[int, int, int, int], *, name: str = "region") -> Path:
        """Capture a desktop-coordinate region, usually resolved from an accessible component."""
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise VisionUnavailable("Pillow is required for screen capture") from exc
        x, y, width, height = bounds
        if width <= 0 or height <= 0:
            raise ValueError("capture bounds must have positive width and height")
        try:
            image = ImageGrab.grab().crop((x, y, x + width, y + height))
        except Exception as exc:
            raise VisionUnavailable(f"screen capture failed: {exc}") from exc
        screenshots = self.context.run_dir / "screenshots"
        screenshots.mkdir(parents=True, exist_ok=True)
        path = _unique_path(screenshots, name, ".png")
        image.save(path)
        self.context.evidence.record("region_captured", bounds=list(bounds), path=str(path.relative_to(self.context.run_dir)))
        return path

    def compare_baseline(
        self,
        baseline: Path,
        *,
        bounds: tuple[int, int, int, int] | None = None,
        mask: Path | None = None,
        pixel_tolerance: int = 12,
        max_difference_ratio: float = 0.01,
        name: str = "baseline",
    ) -> BaselineComparison:
        """Compare a screenshot against an approved baseline and retain diff evidence.

        White mask pixels participate in comparison; black pixels are ignored.
        """
        from PIL import Image, ImageChops, ImageGrab

        if not baseline.is_file():
            raise FileNotFoundError(f"baseline image does not exist: {baseline}")
        if pixel_tolerance < 0 or max_difference_ratio < 0 or max_difference_ratio > 1:
            raise ValueError("invalid baseline comparison tolerance")
        try:
            actual_image = ImageGrab.grab().convert("RGB")
        except Exception as exc:
            raise VisionUnavailable(f"screen capture failed: {exc}") from exc
        if bounds is not None:
            x, y, width, height = bounds
            actual_image = actual_image.crop((x, y, x + width, y + height))
        expected_image = Image.open(baseline).convert("RGB")
        if actual_image.size != expected_image.size:
            raise AssertionError(f"baseline size {expected_image.size} does not match actual size {actual_image.size}")
        mask_image = None
        if mask is not None:
            if not mask.is_file():
                raise FileNotFoundError(f"baseline mask does not exist: {mask}")
            mask_image = Image.open(mask).convert("L")
            if mask_image.size != actual_image.size:
                raise AssertionError(f"baseline mask size {mask_image.size} does not match actual size {actual_image.size}")
        difference = ImageChops.difference(actual_image, expected_image)
        pixels = difference.load()
        active_mask = mask_image.load() if mask_image is not None else None
        changed = compared = 0
        for y in range(difference.height):
            for x in range(difference.width):
                if active_mask is not None and active_mask[x, y] == 0:
                    continue
                compared += 1
                if max(pixels[x, y]) > pixel_tolerance:
                    changed += 1
        ratio = changed / max(1, compared)
        vision_dir = self.context.run_dir / "vision"
        vision_dir.mkdir(parents=True, exist_ok=True)
        actual_path = _unique_path(vision_dir, f"{name}-actual", ".png")
        expected_path = _unique_path(vision_dir, f"{name}-expected", ".png")
        diff_path = _unique_path(vision_dir, f"{name}-diff", ".png")
        actual_image.save(actual_path)
        expected_image.save(expected_path)
        difference.save(diff_path)
        comparison = BaselineComparison(changed, compared, ratio, actual_path, expected_path, diff_path)
        self.context.evidence.record(
            "baseline_compared",
            baseline=str(baseline),
            bounds=list(bounds) if bounds else None,
            mask=str(mask) if mask else None,
            changed_pixels=changed,
            compared_pixels=compared,
            difference_ratio=ratio,
            actual=str(actual_path.relative_to(self.context.run_dir)),
            expected=str(expected_path.relative_to(self.context.run_dir)),
            diff=str(diff_path.relative_to(self.context.run_dir)),
        )
        if ratio > max_difference_ratio:
            raise AssertionError(
                f"baseline difference ratio {ratio:.4%} exceeds allowed {max_difference_ratio:.4%}; diff={diff_path}"
            )
        return comparison

    def compare_component_baseline(self, component, *, profile: VisualProfile | None = None) -> BaselineComparison:
        """Compare a resolved logical component to its exact approved visual variant."""
        resolved = component.resolve()
        bounds = resolved.metadata.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise VisualBaselineError(
                f"component {component.definition.component_id!r} did not resolve desktop bounds for visual comparison"
            )
        try:
            bounds = tuple(int(value) for value in bounds)
        except (TypeError, ValueError) as exc:
            raise VisualBaselineError("component visual bounds are invalid") from exc
        if bounds[2] <= 0 or bounds[3] <= 0:
            raise VisualBaselineError("component visual bounds must be positive")
        key, variant = select_visual_variant(component.definition, profile)
        source = component.definition.repository_path
        if source is None:
            raise VisualBaselineError(
                f"component {component.definition.component_id!r} has visual metadata but no repository source path"
            )
        root = source.parent.resolve()
        baseline = (root / variant["image"]).resolve()
        mask = (root / variant["mask"]).resolve() if variant.get("mask") else None
        visual_root = (root / "visual").resolve()
        if visual_root not in baseline.parents or (mask is not None and visual_root not in mask.parents):
            raise VisualBaselineError("component visual baseline escapes repository visual directory")
        comparison = self.compare_baseline(
            baseline, bounds=bounds, mask=mask,
            pixel_tolerance=int(variant["pixel_tolerance"]),
            max_difference_ratio=float(variant["max_difference_ratio"]),
            name=f"{component.definition.component_id}-visual",
        )
        self.context.evidence.record(
            "component_visual_compared", component_id=component.definition.component_id,
            variant_key=key, visual_revision=(component.definition.visual or {}).get("revision", 0),
        )
        return comparison


    def wait_for_color(
        self,
        rgb: tuple[int, int, int],
        *,
        tolerance: int = 12,
        bounds: tuple[int, int, int, int] | None = None,
        name: str = "color-search",
        min_pixels: int = 16,
        timeout: float = 2.0,
        interval: float = 0.05,
    ) -> VisionMatch:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return self.find_color(
                    rgb,
                    tolerance=tolerance,
                    bounds=bounds,
                    name=name,
                    min_pixels=min_pixels,
                    record_no_match=False,
                )
            except LookupError as exc:
                last_error = exc
                time.sleep(interval)
        self.context.evidence.record(
            "vision_wait_timeout",
            operation="find_color",
            rgb=list(rgb),
            tolerance=tolerance,
            timeout=timeout,
            error=str(last_error) if last_error else None,
        )
        raise LookupError(f"timed out waiting for color rgb={rgb}: {last_error}")

    def find_color(
        self,
        rgb: tuple[int, int, int],
        *,
        tolerance: int = 12,
        bounds: tuple[int, int, int, int] | None = None,
        name: str = "color-search",
        min_pixels: int = 16,
        record_no_match: bool = True,
    ) -> VisionMatch:
        try:
            from PIL import ImageGrab
        except ImportError as exc:
            raise VisionUnavailable("Pillow is required for screen capture") from exc
        try:
            full = ImageGrab.grab().convert("RGB")
        except Exception as exc:
            raise VisionUnavailable(f"screen capture failed: {exc}") from exc

        origin_x = 0
        origin_y = 0
        image = full
        if bounds is not None:
            x, y, width, height = bounds
            origin_x, origin_y = x, y
            image = full.crop((x, y, x + width, y + height))

        target_r, target_g, target_b = rgb
        min_x = image.width
        min_y = image.height
        max_x = -1
        max_y = -1
        count = 0
        total_distance = 0.0
        pixels = image.load()
        for y in range(image.height):
            for x in range(image.width):
                r, g, b = pixels[x, y]
                distance = math.sqrt((r - target_r) ** 2 + (g - target_g) ** 2 + (b - target_b) ** 2)
                if distance <= tolerance:
                    count += 1
                    total_distance += distance
                    min_x = min(min_x, x)
                    min_y = min(min_y, y)
                    max_x = max(max_x, x)
                    max_y = max(max_y, y)
        if count < min_pixels or max_x < min_x or max_y < min_y:
            if record_no_match:
                no_match_dir = self.context.run_dir / "vision"
                no_match_dir.mkdir(parents=True, exist_ok=True)
                no_match_path = _unique_path(no_match_dir, f"{name}-no-match", ".png")
                full.save(no_match_path)
                self.context.evidence.record(
                    "vision_no_match",
                    operation="find_color",
                    rgb=list(rgb),
                    tolerance=tolerance,
                    pixels=count,
                    evidence=str(no_match_path.relative_to(self.context.run_dir)),
                )
            raise LookupError(f"no color region found for rgb={rgb} tolerance={tolerance}; matched_pixels={count}")

        average_distance = total_distance / count
        max_distance = max(1.0, float(tolerance))
        confidence = max(0.0, min(1.0, 1.0 - average_distance / max_distance))
        match = VisionMatch(
            x=origin_x + min_x,
            y=origin_y + min_y,
            width=max_x - min_x + 1,
            height=max_y - min_y + 1,
            confidence=confidence,
        )
        evidence_path = self._save_annotated(full, match, name=name)
        self.context.evidence.record(
            "vision_match",
            operation="find_color",
            rgb=list(rgb),
            tolerance=tolerance,
            pixels=count,
            match={
                "x": match.x,
                "y": match.y,
                "width": match.width,
                "height": match.height,
                "confidence": match.confidence,
            },
            evidence=str(evidence_path.relative_to(self.context.run_dir)),
        )
        return match

    def _save_annotated(self, image, match: VisionMatch, *, name: str) -> Path:
        from PIL import ImageDraw

        vision_dir = self.context.run_dir / "vision"
        vision_dir.mkdir(parents=True, exist_ok=True)
        path = _unique_path(vision_dir, name, ".png")
        annotated = image.copy()
        draw = ImageDraw.Draw(annotated)
        draw.rectangle(
            (match.x, match.y, match.x + match.width - 1, match.y + match.height - 1),
            outline="white",
            width=2,
        )
        annotated.save(path)
        return path


def _unique_path(directory: Path, stem: str, suffix: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in stem).strip("-") or "artifact"
    candidate = directory / f"{safe}{suffix}"
    index = 2
    while candidate.exists():
        candidate = directory / f"{safe}-{index}{suffix}"
        index += 1
    return candidate
