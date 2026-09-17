from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from automation_harness.authoring.object_reference_updates import (
    apply_project_reference_updates,
    normalize_rename_map,
    preview_project_reference_updates,
)
from automation_harness.authoring.plan_repository import assign_repository
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.authoring.step_registry import AuthoringStepRegistry, save_step_registry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.core.test_plan import load_plan, save_plan
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.plan import StepCall, TestPlan


def _definition(component_id):
    return ComponentDefinition(
        component_id=component_id,
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"name": component_id}}}),),
    )


def _plan(name, component_id):
    return TestPlan(
        name=name,
        steps=(StepCall(
            node_id="click",
            step_id="gui.object.action",
            inputs={"component_id": component_id, "action": {"type": "click"}},
        ),),
    )


def _reusable(component_id):
    return ReusableStepDefinition(
        step_id="shared.click",
        name="Shared Click",
        description="Click the shared object",
        plan=_plan("Reusable", component_id),
        inputs={},
        outputs={},
    )


def _project(tmp_path, repository, plan_path, registry_path):
    project_path = tmp_path / "suite.ahproject"
    project = AuthoringProject(
        name="Suite",
        root=tmp_path,
        test_plans=(plan_path,),
        step_registries=(registry_path,),
        object_repositories=(repository,),
    )
    save_authoring_project(project_path, project)
    return project_path


def test_normalize_rename_map_composes_reparents():
    assert normalize_rename_map({"A.Button": "B.Button", "B.Button": "C.Button"}) == {
        "A.Button": "C.Button",
        "B.Button": "C.Button",
    }


def test_preview_finds_plan_and_registry_bound_to_repository(tmp_path: Path):
    repository_path = tmp_path / "objects.ahobjects"
    ComponentRepository({"A.Button": _definition("A.Button")}).save(repository_path)

    plan_path = tmp_path / "flow.ahplan"
    save_plan(assign_repository(_plan("Flow", "A.Button"), plan_path, repository_path), plan_path)

    registry_path = tmp_path / "steps.ahregistry"
    save_step_registry(registry_path, AuthoringStepRegistry(
        name="Steps", root=tmp_path, repository=repository_path, steps=(_reusable("A.Button"),),
    ))
    project_path = _project(tmp_path, repository_path, plan_path, registry_path)

    report = preview_project_reference_updates(
        project_path, repository_path, {"A.Button": "B.Button"},
    )
    kinds = {item.artifact_type for item in report.updates}
    assert kinds == {"test_plan", "step_registry"}
    assert report.replacement_count >= 2


def test_apply_updates_repository_plan_and_registry_as_one_operation(tmp_path: Path):
    repository_path = tmp_path / "objects.ahobjects"
    old = _definition("A.Button")
    ComponentRepository({"A.Button": old}).save(repository_path)

    plan_path = tmp_path / "flow.ahplan"
    save_plan(assign_repository(_plan("Flow", "A.Button"), plan_path, repository_path), plan_path)

    registry_path = tmp_path / "steps.ahregistry"
    save_step_registry(registry_path, AuthoringStepRegistry(
        name="Steps", root=tmp_path, repository=repository_path, steps=(_reusable("A.Button"),),
    ))
    project_path = _project(tmp_path, repository_path, plan_path, registry_path)

    moved = ComponentRepository({
        "B.Button": replace(old, component_id="B.Button", revision=old.revision + 1),
    })
    report = apply_project_reference_updates(
        project_path,
        repository_path,
        {"A.Button": "B.Button"},
        repository=moved,
    )

    persisted_repository = ComponentRepository.load((repository_path,))
    assert "B.Button" in persisted_repository.components
    assert persisted_repository.get("B.Button").object_id == old.object_id

    persisted_plan = load_plan(plan_path)
    assert persisted_plan.steps[0].inputs["component_id"] == "B.Button"

    persisted_registry = AuthoringStepRegistry.load(registry_path)
    assert persisted_registry.steps[0].plan.steps[0].inputs["component_id"] == "B.Button"
    assert {item.artifact_type for item in report.updates} == {
        "object_repository", "test_plan", "step_registry",
    }


def test_unrelated_repository_artifacts_are_not_rewritten(tmp_path: Path):
    repository_path = tmp_path / "objects.ahobjects"
    other_repository = tmp_path / "other.ahobjects"
    ComponentRepository({"A.Button": _definition("A.Button")}).save(repository_path)
    ComponentRepository({"A.Button": _definition("A.Button")}).save(other_repository)

    plan_path = tmp_path / "flow.ahplan"
    save_plan(assign_repository(_plan("Flow", "A.Button"), plan_path, other_repository), plan_path)

    registry_path = tmp_path / "steps.ahregistry"
    save_step_registry(registry_path, AuthoringStepRegistry(
        name="Steps", root=tmp_path, repository=other_repository, steps=(),
    ))
    project_path = _project(tmp_path, repository_path, plan_path, registry_path)

    report = preview_project_reference_updates(project_path, repository_path, {"A.Button": "B.Button"})
    assert report.updates == ()
    assert load_plan(plan_path).steps[0].inputs["component_id"] == "A.Button"
