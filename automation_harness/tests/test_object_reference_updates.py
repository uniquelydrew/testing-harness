from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from automation_harness.authoring.object_reference_updates import (
    apply_project_reference_updates,
    normalize_rename_map,
    preview_project_reference_updates,
)
from automation_harness.authoring import object_reference_updates
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


def test_immutable_object_reference_requires_no_alias_rewrite():
    definition = _definition("A.Button")
    plan = _plan("Flow", definition.object_id)

    rewritten = object_reference_updates._rewrite_plan(
        plan, {"A.Button": "B.Button"},
    )[0]

    assert rewritten.steps[0].inputs["component_id"] == definition.object_id


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


def test_transaction_restores_all_files_when_a_commit_replace_fails(tmp_path: Path, monkeypatch):
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
    originals = {path: path.read_bytes() for path in (repository_path, plan_path, registry_path)}
    real_replace = object_reference_updates.os.replace
    update_replaces = {"count": 0}

    def fail_second_update(source, destination):
        if ".update-" in Path(source).name:
            update_replaces["count"] += 1
            if update_replaces["count"] == 2:
                raise OSError("injected commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(object_reference_updates.os, "replace", fail_second_update)
    moved = ComponentRepository({
        "B.Button": replace(old, component_id="B.Button", revision=old.revision + 1),
    })

    with pytest.raises(OSError, match="injected commit failure"):
        apply_project_reference_updates(
            project_path, repository_path, {"A.Button": "B.Button"}, repository=moved,
        )

    assert {path: path.read_bytes() for path in originals} == originals


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
