from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


@dataclass(frozen=True)
class PlanVariableRef:
    path: str

    def to_dict(self) -> dict[str, str]:
        return {"$var": self.path}


@dataclass(frozen=True)
class StepCall:
    node_id: str
    step_id: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    outputs: Mapping[str, str] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.node_id,
            "step": self.step_id,
            "description": self.description,
            "inputs": _encode_refs(dict(self.inputs)),
            "outputs": dict(self.outputs),
            "depends_on": list(self.depends_on),
        }


@dataclass(frozen=True)
class TestPlan:
    __test__ = False
    name: str
    version: int = 1
    variables: Mapping[str, Any] = field(default_factory=dict)
    steps: tuple[StepCall, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "variables": _encode_refs(dict(self.variables)),
            "steps": [step.to_dict() for step in self.steps],
        }


class StepStatus(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    READY = "ready"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass
class StepExecutionState:
    node_id: str
    step_id: str
    status: StepStatus = StepStatus.PENDING
    unresolved_variables: tuple[str, ...] = ()
    resolved_inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    attempts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "step_id": self.step_id,
            "status": self.status.value,
            "unresolved_variables": list(self.unresolved_variables),
            "resolved_inputs": self.resolved_inputs,
            "outputs": self.outputs,
            "error": self.error,
            "attempts": self.attempts,
        }


@dataclass
class ExecutionState:
    plan_name: str
    variables: dict[str, Any]
    steps: dict[str, StepExecutionState]

    @classmethod
    def from_plan(cls, plan: TestPlan) -> "ExecutionState":
        return cls(
            plan_name=plan.name,
            variables=dict(plan.variables),
            steps={
                item.node_id: StepExecutionState(node_id=item.node_id, step_id=item.step_id)
                for item in plan.steps
            },
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_name": self.plan_name,
            "variables": self.variables,
            "steps": {key: value.to_dict() for key, value in self.steps.items()},
        }


def _encode_refs(value: Any) -> Any:
    if isinstance(value, PlanVariableRef):
        return value.to_dict()
    if isinstance(value, list):
        return [_encode_refs(item) for item in value]
    if isinstance(value, tuple):
        return [_encode_refs(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode_refs(item) for key, item in value.items()}
    return value
