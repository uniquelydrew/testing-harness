from pathlib import Path

from automation_harness.authoring.plan_repository import (
    assigned_repository_path,
    assign_repository,
    ensure_default_repository,
    materialize_captured_target,
    merge_objects_or,
)
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_plan import load_plan, save_plan
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy
from automation_harness.models.gui import ObjectType
from automation_harness.models.plan import TestPlan


def _capture(name="Submit", accessible_id="submit"):
    return CapturedComponent(
        name=name,
        role="push button",
        description="Submit button",
        accessible_id=accessible_id,
        application="demo",
        hierarchy=("window", "button"),
        actions=("activate",),
        bounds=(10, 20, 100, 30),
        state=ComponentState(present=True),
        window="Demo",
        object_type=ObjectType.BUTTON,
        framework="atspi",
    )


def test_repository_assignment_round_trips_through_plan_metadata(tmp_path):
    plan_path = tmp_path / "login.ahplan"
    repository_path = tmp_path / "objects.ahobjects"
    ComponentRepository({}).save(repository_path)
    plan = assign_repository(TestPlan(name="login"), plan_path, repository_path)
    save_plan(plan, plan_path)

    loaded = load_plan(plan_path)
    assert assigned_repository_path(loaded, plan_path) == repository_path.resolve()


def test_default_repository_is_created_next_to_plan(tmp_path):
    plan_path = tmp_path / "login.ahplan"
    plan, repository_path = ensure_default_repository(TestPlan(name="login"), plan_path)
    assert repository_path == tmp_path / "login.ahobjects"
    assert repository_path.is_file()
    assert assigned_repository_path(plan, plan_path) == repository_path.resolve()


def test_repeated_capture_reuses_same_repository_object():
    repository = ComponentRepository({})
    repository, first_id, created = materialize_captured_target(repository, _capture())
    assert created is True
    repository, second_id, created = materialize_captured_target(repository, _capture())
    assert created is False
    assert second_id == first_id
    assert len(repository.components) == 1


def test_or_merge_unions_locator_strategies_and_removes_duplicate():
    one = ComponentDefinition(
        "submit",
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"accessible_id": "submit"}}}),),
        object_type=ObjectType.BUTTON,
        framework="atspi",
    )
    two = ComponentDefinition(
        "submit-alt",
        strategies=(ComponentStrategy("javafx", {"identification": {"fx_id": "submit"}}),),
        object_type=ObjectType.BUTTON,
        framework="javafx",
    )
    repository = ComponentRepository({"submit": one, "submit-alt": two})
    merged = merge_objects_or(repository, "submit", ("submit-alt",))
    assert "submit-alt" not in merged.components
    assert len(merged.get("submit").strategies) == 2
    assert merged.get("submit").object_id == one.object_id
