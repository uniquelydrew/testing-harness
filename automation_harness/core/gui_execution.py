"""Extensible semantic-action execution primitives.

ComponentHandle currently supplies the accessibility executor.  This registry
allows optional Java-agent, HID, OS-input, and visual executors to be added
without widening the declarative test API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from automation_harness.models.gui import ExecutionResult, GuiAction


class GuiActionExecutor(Protocol):
    name: str
    def supports(self, target: Any, action: GuiAction) -> bool: ...
    def execute(self, target: Any, action: GuiAction) -> ExecutionResult: ...


@dataclass
class ExecutionStrategyResolver:
    executors: list[GuiActionExecutor] = field(default_factory=list)

    def resolve(self, target: Any, action: GuiAction, requested: str | None = None) -> list[GuiActionExecutor]:
        available = [executor for executor in self.executors if executor.supports(target, action)]
        if requested is None:
            return available
        requested_matches = [executor for executor in available if executor.name == requested]
        if not requested_matches:
            raise LookupError(f"requested GUI execution strategy {requested!r} is unavailable")
        return requested_matches

    def execute(self, target: Any, action: GuiAction, requested: str | None = None) -> ExecutionResult:
        attempts: list[dict[str, Any]] = []
        for executor in self.resolve(target, action, requested):
            try:
                result = executor.execute(target, action)
                return ExecutionResult(result.action, result.strategy, result.result, (*attempts, *result.attempts))
            except Exception as exc:
                attempts.append({"strategy": executor.name, "error": f"{type(exc).__name__}: {exc}"})
        raise RuntimeError(f"all GUI execution strategies failed for {action.type.value}: {attempts}")
