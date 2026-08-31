"""Action-specific policy for retaining reproduction evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from automation_harness.models.component import CapturedComponent
from automation_harness.models.gui import ActionType


@dataclass(frozen=True)
class EvidencePolicy:
    action: ActionType
    requires_target: bool = True
    retains_coordinates: bool = False
    requires_resulting_value: bool = False


def policy_for(action: ActionType, target: CapturedComponent | None = None) -> EvidencePolicy:
    semantic_type = target.semantic_type().value if target else ""
    if action == ActionType.SET_TEXT:
        return EvidencePolicy(action, requires_resulting_value=True)
    if action == ActionType.DRAG:
        return EvidencePolicy(action, retains_coordinates=True)
    if action in {ActionType.CLICK, ActionType.RIGHT_CLICK} and semantic_type in {"canvas", "graphic"}:
        return EvidencePolicy(action, retains_coordinates=True)
    return EvidencePolicy(action)


def parameters_for_pointer(action: ActionType, target: CapturedComponent | None, coordinates: tuple[int, int] | None) -> dict[str, Any]:
    policy = policy_for(action, target)
    if policy.retains_coordinates and coordinates is not None:
        return {"coordinates": list(coordinates)}
    return {}
