from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from automation_harness.core.test_context import TestContext


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
