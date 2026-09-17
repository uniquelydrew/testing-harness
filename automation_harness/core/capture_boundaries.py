"""Evidence-driven classification of concrete Java rendering boundaries."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping

from automation_harness.models.component import CapturedComponent
from automation_harness.models.component import ComponentStrategy


class CaptureBoundaryKind(str, Enum):
    SWING = "swing"
    JAVA_FX = "javafx"
    JOGL_SURFACE = "jogl_surface"
    BATIK_SURFACE = "batik_surface"
    CUSTOM_RENDER_SURFACE = "custom_render_surface"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CaptureBoundary:
    kind: CaptureBoundaryKind
    framework: str
    native_class: str | None
    bounds: tuple[int, int, int, int] | None
    supports_native_children: bool
    supports_visual_children: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "framework": self.framework,
            "native_class": self.native_class,
            "bounds": list(self.bounds) if self.bounds else None,
            "supports_native_children": self.supports_native_children,
            "supports_visual_children": self.supports_visual_children,
        }


_JOGL_PREFIXES = ("com.jogamp.opengl.", "javax.media.opengl.")
_JOGL_NAMES = ("glcanvas", "gljpanel", "newtcanvasawt")
_BATIK_PREFIXES = ("org.apache.batik.",)
_JAVAFX_PREFIXES = ("javafx.", "com.sun.javafx.")
_SWING_PREFIXES = ("javax.swing.", "java.awt.", "sun.awt.")


def classify_capture_boundary(captured: CapturedComponent) -> CaptureBoundary:
    """Classify only from positive runtime evidence; failure is not JavaFX."""
    native_class = str(captured.native_class or "").strip()
    folded = native_class.casefold()
    framework = str(captured.framework or "").strip().casefold()
    properties: Mapping[str, Any] = captured.backend_properties or {}

    if framework == "javafx" or folded.startswith(_JAVAFX_PREFIXES):
        kind = CaptureBoundaryKind.JAVA_FX
        return CaptureBoundary(kind, "javafx", native_class or None, captured.bounds, True, False)
    if framework == "jogl" or folded.startswith(_JOGL_PREFIXES) or any(
        folded.endswith("." + name) or folded == name for name in _JOGL_NAMES
    ):
        kind = CaptureBoundaryKind.JOGL_SURFACE
        return CaptureBoundary(kind, "jogl", native_class or None, captured.bounds, False, True)
    if framework == "batik" or folded.startswith(_BATIK_PREFIXES):
        kind = CaptureBoundaryKind.BATIK_SURFACE
        return CaptureBoundary(kind, "batik", native_class or None, captured.bounds, False, True)
    if properties.get("opaque_render_surface") is True:
        kind = CaptureBoundaryKind.CUSTOM_RENDER_SURFACE
        return CaptureBoundary(kind, framework or "custom", native_class or None, captured.bounds, False, True)
    if framework in {"swing", "awt", "java"} or folded.startswith(_SWING_PREFIXES):
        kind = CaptureBoundaryKind.SWING
        return CaptureBoundary(kind, framework or "swing", native_class or None, captured.bounds, True, False)
    return CaptureBoundary(CaptureBoundaryKind.UNKNOWN, framework or "unknown", native_class or None, captured.bounds, True, False)


def annotate_capture_boundary(captured: CapturedComponent) -> CapturedComponent:
    """Persist classification evidence on a capture without changing locator identity."""
    from dataclasses import replace

    boundary = classify_capture_boundary(captured)
    properties = dict(captured.backend_properties or {})
    properties["capture_boundary"] = boundary.to_dict()
    framework = captured.framework
    if boundary.kind in {
        CaptureBoundaryKind.JOGL_SURFACE,
        CaptureBoundaryKind.BATIK_SURFACE,
        CaptureBoundaryKind.CUSTOM_RENDER_SURFACE,
    }:
        framework = boundary.framework
    return replace(captured, framework=framework, backend_properties=properties)


def surface_relative_visual_capture(captured: CapturedComponent, *, region_size: int = 64) -> CapturedComponent:
    """Create an owner-relative visual leaf from a positively identified surface."""
    boundary = classify_capture_boundary(captured)
    if not boundary.supports_visual_children:
        raise ValueError("captured object is not a visual rendering boundary")
    if captured.bounds is None:
        raise ValueError("rendering boundary has no screen bounds")
    point = dict(captured.backend_properties or {}).get("capture_point")
    if not isinstance(point, (list, tuple)) or len(point) != 2:
        raise ValueError("rendering-boundary capture has no pointer coordinates")
    left, top, width, height = captured.bounds
    px, py = int(point[0]), int(point[1])
    size = max(8, int(region_size))
    x = max(left, min(px - size // 2, left + width - 1))
    y = max(top, min(py - size // 2, top + height - 1))
    w = max(1, min(size, left + width - x))
    h = max(1, min(size, top + height - y))
    anchor = captured.candidate_identification().to_dict()
    strategy = ComponentStrategy("anchored_visual", {
        "anchor_identification": anchor,
        "relative_bounds": [
            (x - left) / width, (y - top) / height, w / width, h / height,
        ],
    })
    properties = dict(captured.backend_properties or {})
    properties["anchor_bounds"] = list(captured.bounds)
    properties["capture_boundary"] = boundary.to_dict()
    source_strategy = captured.candidate_strategy()
    properties["surface_strategy"] = {
        "type": source_strategy.type,
        **dict(source_strategy.options),
    }
    return replace(
        captured,
        name="Visual target",
        role="visual component",
        accessible_id=None,
        actions=(),
        bounds=(x, y, w, h),
        authored_strategy=strategy,
        backend_properties=properties,
        framework="visual",
    )
