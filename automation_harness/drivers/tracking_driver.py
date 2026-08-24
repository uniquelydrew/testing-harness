from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from automation_harness.core.test_context import TestContext
from automation_harness.utils.wait import wait_for


@dataclass
class TrackingDriver:
    context: TestContext

    def _service(self):
        return self.context.require_services().require_tracking()

    def create_track(self, track_id: str, *, x: float, y: float, vx: float, vy: float) -> dict[str, Any]:
        result = self._service().create_track(track_id, x=x, y=y, vx=vx, vy=vy)
        self.context.evidence.record("track_created", track_id=track_id, track=result)
        return result

    def get_track(self, track_id: str) -> dict[str, Any]:
        return self._service().get_track(track_id)

    def follow(self, track_id: str) -> dict[str, Any]:
        result = self._service().follow_track(track_id)
        self.context.evidence.record("track_follow_requested", track_id=track_id, result=result)
        return result

    def set_visibility(self, track_id: str, visible: bool) -> dict[str, Any]:
        result = self._service().set_visibility(track_id, visible)
        self.context.evidence.record("track_visibility_set", track_id=track_id, visible=visible, result=result)
        return result

    def wait_until_moved(self, track_id: str, *, initial_x: float, timeout: float = 2.0) -> dict[str, Any]:
        return wait_for(
            lambda: self.get_track(track_id),
            lambda track: abs(float(track["x"]) - initial_x) > 0.01,
            timeout=timeout,
            description=f"track {track_id} to move",
        )
