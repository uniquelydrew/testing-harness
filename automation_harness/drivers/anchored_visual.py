from __future__ import annotations

from typing import Any, Mapping

from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.models.component import ComponentState, ResolvedComponent


class AnchoredVisualDriver:
    """Resolve visual bounds relative to a durable accessible container."""

    def __init__(self, context=None) -> None:
        self.context = context

    def resolve(self, component_id: str, *, anchor_identification: Mapping[str, Any], relative_bounds) -> ResolvedComponent:
        anchor = AtspiDriver(self.context).resolve("visual-anchor", identification=anchor_identification)
        bounds = anchor.metadata.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise LookupError("visual anchor resolved without desktop bounds")
        if not isinstance(relative_bounds, (list, tuple)) or len(relative_bounds) != 4:
            raise ValueError("anchored_visual.relative_bounds must contain four values")
        left, top, width, height = (int(value) for value in bounds)
        rx, ry, rw, rh = (float(value) for value in relative_bounds)
        if min(rx, ry, rw, rh) < 0 or rx + rw > 1.0001 or ry + rh > 1.0001 or rw <= 0 or rh <= 0:
            raise ValueError("anchored_visual.relative_bounds must be positive normalized anchor coordinates")
        target = (
            left + round(rx * width), top + round(ry * height),
            max(1, round(rw * width)), max(1, round(rh * height)),
        )
        return ResolvedComponent(component_id, "anchored_visual", {
            "bounds": list(target), "anchor_bounds": list(bounds),
        })

    def state(self, *, anchor_identification: Mapping[str, Any], relative_bounds) -> ComponentState:
        resolved = self.resolve("visual-component", anchor_identification=anchor_identification, relative_bounds=relative_bounds)
        return ComponentState(present=True, visible=True, showing=True, properties=resolved.metadata)
