from __future__ import annotations

from pathlib import Path

from automation_harness.backends.base import ExecutionBackend
from automation_harness.core.test_plan import validate_plan_execution
from automation_harness.core.step_registry import default_step_registry
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan
from automation_harness.models.run import BackendHealth
from automation_harness.runner.plan_execution import execute_plan


class FakeBackend(ExecutionBackend):
    name = "fake"

    def __init__(self, *, capabilities=(), risks=("read_only",)) -> None:
        self._capabilities = set(capabilities)
        self._risks = frozenset(risks)
        self.started = False

    @property
    def capabilities(self) -> set[str]:
        return set(self._capabilities)

    @property
    def allowed_step_risks(self) -> frozenset[str]:
        return self._risks

    def start(self, *, run_dir: Path) -> dict[str, str]:
        self.started = True
        return {}

    def health_check(self) -> BackendHealth:
        return BackendHealth(True, self.name, {})

    def stop(self) -> None:
        return None


def test_backend_capability_and_risk_are_validated_before_start(tmp_path: Path):
    plan = TestPlan(
        name="not-authorized",
        steps=(StepCall(node_id="follow", step_id="track.follow", inputs={"track_id": "alpha"}),),
    )
    backend = FakeBackend()
    result = execute_plan(plan, backend, runs_dir=tmp_path)
    assert result.exit_code == 2
    assert backend.started is False
    assert any("requires backend capabilities: tracking" in item for item in result.validation_errors)
    assert any("risk 'synthetic_control' is not authorized" in item for item in result.validation_errors)


def test_runtime_variable_override_is_available_during_preflight_and_execution(tmp_path: Path):
    plan = TestPlan(
        name="runtime-var",
        steps=(
            StepCall(
                node_id="check",
                step_id="validation.equal",
                inputs={"name": "runtime", "actual": PlanVariableRef("runtime_value"), "expected": 7},
            ),
        ),
    )
    backend = FakeBackend()
    result = execute_plan(plan, backend, runs_dir=tmp_path, variable_overrides={"runtime_value": 7})
    assert result.exit_code == 0
    assert backend.started is True
    assert result.passed == 1


def test_validate_plan_execution_accepts_reference_risk_contract():
    plan = TestPlan(
        name="risk",
        steps=(StepCall(node_id="create", step_id="track.create_moving"),),
    )
    issues = validate_plan_execution(
        plan,
        default_step_registry(),
        backend_capabilities={"tracking"},
        allowed_step_risks={"read_only", "synthetic_control"},
    )
    assert issues == []
