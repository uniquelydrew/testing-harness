from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, Sequence


class TrackingService(Protocol):
    def create_track(self, track_id: str, *, x: float, y: float, vx: float, vy: float) -> dict[str, Any]: ...
    def get_track(self, track_id: str) -> dict[str, Any]: ...
    def follow_track(self, track_id: str) -> dict[str, Any]: ...
    def set_visibility(self, track_id: str, visible: bool) -> dict[str, Any]: ...


class ThreatService(Protocol):
    def set_level(self, level: str) -> str: ...
    def get_level(self) -> str: ...


class MosaicService(Protocol):
    def add_tile(self, tile: str) -> list[str]: ...
    def remove_tile(self, tile: str) -> list[str]: ...
    def get_tiles(self) -> list[str]: ...


class TriangulationService(Protocol):
    def triangulate(self, points: Sequence[tuple[float, float]]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class AutomationServices:
    """Typed semantic services consumed by reusable steps and drivers.

    These interfaces are deliberately backend-neutral. The reference environment
    supplies one implementation; other environments can provide adapters later
    without changing the semantic step implementations.
    """

    tracking: TrackingService | None = None
    threat: ThreatService | None = None
    mosaic: MosaicService | None = None
    triangulation: TriangulationService | None = None

    def require_tracking(self) -> TrackingService:
        if self.tracking is None:
            raise RuntimeError("tracking service is unavailable in the current backend")
        return self.tracking

    def require_threat(self) -> ThreatService:
        if self.threat is None:
            raise RuntimeError("threat-state service is unavailable in the current backend")
        return self.threat

    def require_mosaic(self) -> MosaicService:
        if self.mosaic is None:
            raise RuntimeError("mosaic service is unavailable in the current backend")
        return self.mosaic

    def require_triangulation(self) -> TriangulationService:
        if self.triangulation is None:
            raise RuntimeError("triangulation service is unavailable in the current backend")
        return self.triangulation
