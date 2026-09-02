"""Interaction preparation policy and driver-neutral result types.

Preparation establishes desktop preconditions without invoking a component's
semantic default action. Window activation and component focus are intentionally
distinct from ActionType.ACTIVATE.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from automation_harness.models.gui import ActionType, GuiAction


@dataclass(frozen=True)
class PreparationRequirement:
    activate_window: bool
    request_focus: bool
    require_window_activation: bool = True
    require_focus: bool = False


@dataclass(frozen=True)
class PreparationOutcome:
    operation: str
    attempted: bool
    success: bool
    supported: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None

    @classmethod
    def skipped(cls, operation: str, reason: str) -> "PreparationOutcome":
        return cls(operation, False, True, details={"reason": reason})


@dataclass(frozen=True)
class InteractionPreparation:
    strategy: str
    window_activation: PreparationOutcome
    component_focus: PreparationOutcome

    @property
    def successful(self) -> bool:
        return self.window_activation.success and self.component_focus.success

    def to_dict(self) -> dict[str, Any]:
        def serialize(value: PreparationOutcome) -> dict[str, Any]:
            return {
                "operation": value.operation,
                "attempted": value.attempted,
                "success": value.success,
                "supported": value.supported,
                "details": dict(value.details),
                "error": value.error,
            }
        return {
            "strategy": self.strategy,
            "successful": self.successful,
            "window_activation": serialize(self.window_activation),
            "component_focus": serialize(self.component_focus),
        }


_POINTER_ACTIONS = frozenset({
    ActionType.CLICK,
    ActionType.DOUBLE_CLICK,
    ActionType.RIGHT_CLICK,
})
_FOCUS_REQUIRED_ACTIONS = frozenset({
    ActionType.FOCUS,
    ActionType.SET_TEXT,
    ActionType.CLEAR_TEXT,
    ActionType.APPEND_TEXT,
    ActionType.EDIT,
    ActionType.COMMIT_EDIT,
    ActionType.CANCEL_EDIT,
})


def preparation_requirement(action: GuiAction | ActionType | str | Mapping[str, Any]) -> PreparationRequirement:
    """Return the minimum preconditions for one semantic interaction."""
    semantic = GuiAction.from_value(action)
    if semantic.type in _POINTER_ACTIONS:
        return PreparationRequirement(True, False, require_window_activation=True)
    if semantic.type in _FOCUS_REQUIRED_ACTIONS:
        return PreparationRequirement(True, True, require_window_activation=True, require_focus=True)
    return PreparationRequirement(True, True, require_window_activation=True, require_focus=False)


def validate_preparation(
    requirement: PreparationRequirement,
    preparation: InteractionPreparation,
) -> None:
    """Raise when a required interaction precondition was not established."""
    failures: list[str] = []
    if requirement.require_window_activation and not preparation.window_activation.success:
        failures.append(preparation.window_activation.error or "owning window could not be activated")
    if requirement.require_focus and not preparation.component_focus.success:
        failures.append(preparation.component_focus.error or "component focus could not be established")
    if failures:
        raise RuntimeError("interaction preparation failed: " + "; ".join(failures))
