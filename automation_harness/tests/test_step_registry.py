from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation_harness.core.step_registry import StepCapabilityError, StepNotFoundError, default_step_registry
from automation_harness.core.test_context import TestContext
from automation_harness.utils.evidence import EvidenceRecorder


class _Reference:
    def __init__(self) -> None:
        self.level = "LOW"

    def request(self, action: str, **fields):
        if action == "set_threat":
            self.level = fields["level"]
            return {"threat_level": self.level}
        if action == "state":
            return {"threat_level": self.level, "mosaic_tiles": []}
        raise AssertionError(action)


def _context(tmp_path: Path, capabilities: set[str]) -> TestContext:
    from automation_harness.core.component_repository import ComponentRepository

    return TestContext(
        backend="reference",
        run_dir=tmp_path,
        evidence=EvidenceRecorder(tmp_path / "events.jsonl"),
        components=ComponentRepository({}),
        capabilities=frozenset(capabilities),
        steps=default_step_registry(),
        reference=_Reference(),
    )


def test_registry_exposes_stable_step_metadata():
    registry = default_step_registry()
    definition = registry.get("threat.level.set")
    assert definition.domain == "threat"
    assert definition.capabilities == frozenset({"threat-state"})
    assert "level" in str(definition.invocation_signature)
    assert registry.get("set_threat_level") is definition


def test_registry_invocation_reuses_direct_step_implementation(tmp_path: Path):
    ctx = _context(tmp_path, {"threat-state"})
    assert ctx.run_step("threat.level.set", "medium") == "MEDIUM"
    assert ctx.run_step("threat.level.get") == "MEDIUM"

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["event"] for event in events] == [
        "step_started",
        "step_finished",
        "step_started",
        "step_finished",
    ]
    assert events[0]["step"] == "threat.level.set"


def test_registry_rejects_missing_capability_before_step_body(tmp_path: Path):
    ctx = _context(tmp_path, set())
    with pytest.raises(StepCapabilityError, match="threat-state"):
        ctx.run_step("threat.level.set", "HIGH")


def test_registry_suggests_reusable_step_names():
    registry = default_step_registry()
    with pytest.raises(StepNotFoundError, match="threat.level.set"):
        registry.get("threat.lvel.set")
