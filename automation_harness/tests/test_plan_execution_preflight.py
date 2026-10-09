from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError

from automation_harness.backends.base import ExecutionBackend
from automation_harness.core.test_plan import validate_plan_execution
from automation_harness.core.step_registry import default_step_registry
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan
from automation_harness.models.run import BackendHealth
from automation_harness.runner.plan_execution import execute_plan, compose_execution_repository, qualify_plan


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


def _component(name, object_id, owner=None):
    value = {
        "object_id": object_id,
        "object_type": "button",
        "strategies": [{"type": "atspi", "options": {"identification": {"mandatory": {"name": name}}}}],
    }
    if owner is not None:
        value["owner_object_id"] = owner
    return value


def test_execution_composition_restores_legacy_missing_owner():
    parent_id, child_id = str(uuid4()), str(uuid4())
    parent = _component("Parent", parent_id)
    child = _component("Child", child_id, parent_id)
    live = ComponentRepository.from_document({"version": 3, "components": {"Parent": parent, "Child": child}})
    plan = TestPlan(name="legacy", objects={"Child": child})
    result = compose_execution_repository(plan, live)
    assert result.get("Child").owner_object_id == parent_id
    assert result.get("Parent").object_id == parent_id


def test_execution_composition_rejects_same_name_different_uuid():
    live_id, embedded_id = str(uuid4()), str(uuid4())
    live = ComponentRepository.from_document({"version": 3, "components": {"Child": _component("Child", live_id)}})
    plan = TestPlan(name="conflict", objects={"Child": _component("Child", embedded_id)})
    with pytest.raises(ComponentRepositoryError, match="object_id"):
        compose_execution_repository(plan, live)


def test_execution_composition_rejects_uuid_reuse_under_different_name():
    shared_id = str(uuid4())
    live = ComponentRepository.from_document({"version": 3, "components": {"Live": _component("Live", shared_id)}})
    plan = TestPlan(name="conflict", objects={"Embedded": _component("Embedded", shared_id)})
    with pytest.raises(ComponentRepositoryError, match="reuses live object_id"):
        compose_execution_repository(plan, live)


def test_execution_composition_requires_owner_when_no_live_repository():
    plan = TestPlan(name="orphan", objects={"Child": _component("Child", str(uuid4()), str(uuid4()))})
    with pytest.raises(ComponentRepositoryError, match="owner_object_id"):
        compose_execution_repository(plan)


def test_shared_qualifier_rejects_missing_action_reference():
    plan = TestPlan(name="missing", steps=(StepCall(
        node_id="click", step_id="gui.object.action",
        inputs={"component_id": "Not Recorded", "action": {"type": "click"}},
    ),))
    _, _, issues = qualify_plan(plan)
    assert any("unknown component" in issue for issue in issues)


def test_shared_qualifier_recovers_legacy_owner_from_live_repository():
    parent_id, child_id = str(uuid4()), str(uuid4())
    parent = _component("Parent", parent_id)
    child = _component("Child", child_id, parent_id)
    live = ComponentRepository.from_document({"version": 3, "components": {"Parent": parent, "Child": child}})
    plan = TestPlan(name="legacy", objects={"Child": child})
    _, effective, issues = qualify_plan(plan, component_repository=live)
    assert issues == []
    assert effective.get("Parent").object_id == parent_id


def test_execution_returns_validation_error_on_unrecoverable_snapshot(tmp_path: Path):
    plan = TestPlan(name="orphan", objects={"Child": _component("Child", str(uuid4()), str(uuid4()))})
    backend = FakeBackend()
    result = execute_plan(plan, backend, runs_dir=tmp_path)
    assert result.exit_code == 2
    assert backend.started is False
    assert any("owner_object_id" in issue for issue in result.validation_errors)


def _menu_repository():
    menu = {
        "object_id": str(uuid4()),
        "object_type": "menu",
        "actions": ["select_menu_item"],
        "strategies": [{"type": "atspi", "identification": {"mandatory": {"name": "File"}}}],
        "subobjects": {
            "open": {"kind": "menu_item", "selector": {"criteria": {"text": "Open"}}},
            "export": {
                "kind": "menu",
                "selector": {"criteria": {"text": "Export"}},
                "subobjects": {
                    "pdf": {"kind": "menu_item", "selector": {"criteria": {"text": "PDF"}}}
                },
            },
        },
    }
    return ComponentRepository.from_document({"version": 3, "components": {"File": menu}})


@pytest.mark.parametrize("action", [
    {"type": "select_menu_item", "path": ["open"]},
    {"type": "select_menu_item", "path": ["export", "pdf"]},
    {"type": "select_menu_item", "value": "Open"},
    {"type": "select_menu_item", "value": "Export > PDF"},
])
def test_menu_qualification_accepts_canonical_and_legacy_navigation(action):
    repo = _menu_repository()
    plan = TestPlan(name="menu", steps=(StepCall(node_id="choose", step_id="gui.object.action",
        inputs={"component_id": "File", "action": action}),))
    _, _, issues = qualify_plan(plan, component_repository=repo)
    assert issues == []


@pytest.mark.parametrize("action", [
    {"type": "select_menu_item"},
    {"type": "select_menu_item", "path": []},
    {"type": "select_menu_item", "path": ["missing"]},
    {"type": "select_menu_item", "value": ""},
    {"type": "select_menu_item", "value": "Export > Missing"},
])
def test_menu_qualification_rejects_bad_navigation_before_execution(action):
    repo = _menu_repository()
    plan = TestPlan(name="menu", steps=(StepCall(node_id="choose", step_id="gui.object.action",
        inputs={"component_id": "File", "action": action}),))
    _, _, issues = qualify_plan(plan, component_repository=repo)
    assert any("invalid menu navigation" in issue for issue in issues)
