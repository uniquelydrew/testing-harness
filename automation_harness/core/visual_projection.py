"""Runtime-only conversion of semantic results into verified screen geometry."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from automation_harness.core.object_resolution import ObjectResolution, ObjectResolutionError


class VisualProjectionError(ObjectResolutionError):
    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


@dataclass(frozen=True)
class VisualTarget:
    semantic: ObjectResolution
    bounds: tuple[int, int, int, int]
    strategy: str
    details: Mapping[str, Any]


def project_visual_target(service, repository, resolution: ObjectResolution, *, operation: str) -> VisualTarget:
    if resolution.has_visual_bounds:
        return VisualTarget(resolution, resolution.bounds, "resolved_bounds", {})
    raise VisualProjectionError("visual_geometry_unavailable",
        "object resolved semantically but no verified visual representation is available",
        details={"operation": operation, "component_id": resolution.definition.component_id})
