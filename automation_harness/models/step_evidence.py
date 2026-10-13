"""Non-authoritative pre-execution evidence contracts.

Evidence collection may warn, but it must never change execution success or
turn an authored assertion into an implicit assertion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ComponentEvidence:
    component_id: str
    status: str
    image_path: str | None = None
    diagnostic: str | None = None

    def __post_init__(self):
        if self.status not in {"captured", "unavailable"}:
            raise ValueError("invalid component evidence status")
        if self.status == "captured" and not self.image_path:
            raise ValueError("captured evidence requires an image path")

    def to_dict(self) -> dict[str, Any]:
        return dict(component_id=self.component_id, status=self.status,
                    image_path=self.image_path, diagnostic=self.diagnostic)


@dataclass(frozen=True)
class StepEvidenceSummary:
    node_id: str
    attempt_id: str
    components: tuple[ComponentEvidence, ...] = ()
    capture_phase: str = "pre_execution"

    @property
    def evidence_status(self) -> str:
        if not self.components:
            return "not_applicable"
        captured = sum(item.status == "captured" for item in self.components)
        if captured == len(self.components):
            return "complete"
        return "partial" if captured else "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return dict(node_id=self.node_id, attempt_id=self.attempt_id,
                    capture_phase=self.capture_phase,
                    evidence_status=self.evidence_status,
                    expected_components=len(self.components),
                    captured_components=sum(item.status == "captured" for item in self.components),
                    components=[item.to_dict() for item in self.components])


def display_status(execution_status: str, evidence_status: str) -> str:
    """Only a successful execution may be visually downgraded to WARN."""
    if execution_status.lower() in {"passed", "pass"} and evidence_status in {"partial", "unavailable"}:
        return "WARN"
    return {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(
        execution_status.lower(), execution_status.upper()
    )


def referenced_components(inputs: Mapping[str, Any], known_ids) -> tuple[str, ...]:
    """Extract explicit typed component inputs; never guess from arbitrary strings."""
    known = set(known_ids)
    found = []
    def visit(value):
        if isinstance(value, Mapping):
            for key, child in value.items():
                if key in {"component_id", "object_id", "component", "target_component"} and isinstance(child, str):
                    if child in known and child not in found:
                        found.append(child)
                elif isinstance(child, (Mapping, list, tuple)):
                    visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
    visit(inputs)
    return tuple(found)
