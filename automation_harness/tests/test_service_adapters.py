from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.services import AutomationServices
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_context import TestContext
from automation_harness.utils.evidence import EvidenceRecorder


@dataclass
class FakeThreatService:
    level: str = "LOW"

    def set_level(self, level: str) -> str:
        self.level = level.upper()
        return self.level

    def get_level(self) -> str:
        return self.level


class FakeTrackingService:
    def __init__(self):
        self.tracks = {}

    def create_track(self, track_id, *, x, y, vx, vy):
        value = {"track_id": track_id, "x": x, "y": y, "vx": vx, "vy": vy, "visible": True, "followed": False}
        self.tracks[track_id] = value
        return dict(value)

    def get_track(self, track_id):
        return dict(self.tracks[track_id])

    def follow_track(self, track_id):
        self.tracks[track_id]["followed"] = True
        return dict(self.tracks[track_id])

    def set_visibility(self, track_id, visible):
        self.tracks[track_id]["visible"] = bool(visible)
        return dict(self.tracks[track_id])


def _context(tmp_path: Path, services: AutomationServices, capabilities: set[str]) -> TestContext:
    evidence = EvidenceRecorder(tmp_path / "events.jsonl")
    return TestContext(
        backend="adapter-test",
        run_dir=tmp_path,
        evidence=evidence,
        components=ComponentRepository({}),
        capabilities=frozenset(capabilities),
        steps=default_step_registry(),
        services=services,
    )


def test_threat_steps_use_injected_semantic_service_without_reference_client(tmp_path: Path):
    threat = FakeThreatService()
    ctx = _context(tmp_path, AutomationServices(threat=threat), {"threat-state"})
    assert ctx.reference is None
    assert ctx.run_step("threat.level.set", "high") == "HIGH"
    assert ctx.run_step("threat.level.get") == "HIGH"


def test_tracking_steps_use_injected_semantic_service_without_reference_client(tmp_path: Path):
    tracking = FakeTrackingService()
    ctx = _context(tmp_path, AutomationServices(tracking=tracking), {"tracking"})
    created = ctx.run_step("track.create_moving", "alpha", x=1.0, y=2.0, vx=3.0, vy=0.0)
    followed = ctx.run_step("track.follow", "alpha")
    hidden = ctx.run_step("camera.track.set_visibility", "alpha", False)
    assert created["track_id"] == "alpha"
    assert followed["followed"] is True
    assert hidden["visible"] is False
