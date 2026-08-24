from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from automation_harness.core.services import AutomationServices
from automation_harness.reference.protocol import ReferenceClient


@dataclass(frozen=True)
class ReferenceTrackingService:
    client: ReferenceClient

    def create_track(self, track_id: str, *, x: float, y: float, vx: float, vy: float) -> dict[str, Any]:
        return dict(self.client.request("create_track", track_id=track_id, x=x, y=y, vx=vx, vy=vy))

    def get_track(self, track_id: str) -> dict[str, Any]:
        return dict(self.client.request("get_track", track_id=track_id))

    def follow_track(self, track_id: str) -> dict[str, Any]:
        return dict(self.client.request("follow_track", track_id=track_id))

    def set_visibility(self, track_id: str, visible: bool) -> dict[str, Any]:
        return dict(self.client.request("set_track_visible", track_id=track_id, visible=visible))


@dataclass(frozen=True)
class ReferenceThreatService:
    client: ReferenceClient

    def set_level(self, level: str) -> str:
        result = self.client.request("set_threat", level=level.upper())
        return str(result["threat_level"])

    def get_level(self) -> str:
        return str(self.client.request("state")["threat_level"])


@dataclass(frozen=True)
class ReferenceMosaicService:
    client: ReferenceClient

    def add_tile(self, tile: str) -> list[str]:
        return list(self.client.request("add_tile", tile=tile))

    def remove_tile(self, tile: str) -> list[str]:
        return list(self.client.request("remove_tile", tile=tile))

    def get_tiles(self) -> list[str]:
        return list(self.client.request("state")["mosaic_tiles"])


@dataclass(frozen=True)
class ReferenceTriangulationService:
    client: ReferenceClient

    def triangulate(self, points: Sequence[tuple[float, float]]) -> dict[str, Any]:
        serialized = [[float(x), float(y)] for x, y in points]
        return dict(self.client.request("triangulate", points=serialized))


def services_for_reference(client: ReferenceClient) -> AutomationServices:
    return AutomationServices(
        tracking=ReferenceTrackingService(client),
        threat=ReferenceThreatService(client),
        mosaic=ReferenceMosaicService(client),
        triangulation=ReferenceTriangulationService(client),
    )
