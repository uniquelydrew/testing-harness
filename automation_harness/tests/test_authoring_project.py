from dataclasses import fields

import yaml
import pytest

from automation_harness.authoring.project import AuthoringProject, ProjectError, create_authoring_project, save_authoring_project


def test_project_round_trip_contains_artifact_membership(tmp_path):
    path = tmp_path / "project.ahproject"
    created = create_authoring_project(path, "Authoring smoke test")
    loaded = AuthoringProject.load(path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert loaded == created
    assert {item.name for item in fields(AuthoringProject)} == {
        "name", "root", "test_plans", "step_registries", "object_repositories"
    }
    assert document == {
        "version": 2,
        "name": "Authoring smoke test",
        "test_plans": [],
        "step_registries": [],
        "object_repositories": [],
    }


def test_project_tracks_independent_child_artifacts(tmp_path):
    project_path = tmp_path / "project.ahproject"
    plan = tmp_path / "tests" / "login.ahplan"
    registry = tmp_path / "steps" / "common.ahregistry"
    repository = tmp_path / "objects" / "common.ahobjects"
    for child in (plan, registry, repository):
        child.parent.mkdir(parents=True, exist_ok=True)
        child.write_text("placeholder", encoding="utf-8")

    project = create_authoring_project(project_path, "Example")
    project = project.with_test_plan(plan).with_step_registry(registry).with_object_repository(repository)
    save_authoring_project(project_path, project)

    loaded = AuthoringProject.load(project_path)
    assert loaded.test_plans == (plan.resolve(),)
    assert loaded.step_registries == (registry.resolve(),)
    assert loaded.object_repositories == (repository.resolve(),)

    document = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    assert document["test_plans"] == ["tests/login.ahplan"]
    assert document["step_registries"] == ["steps/common.ahregistry"]
    assert document["object_repositories"] == ["objects/common.ahobjects"]


def test_removing_project_membership_does_not_delete_artifact(tmp_path):
    project_path = tmp_path / "project.ahproject"
    plan = tmp_path / "login.ahplan"
    plan.write_text("placeholder", encoding="utf-8")
    project = create_authoring_project(project_path, "Example").with_test_plan(plan)

    project = project.without_test_plan(plan)

    assert project.test_plans == ()
    assert plan.is_file()


def test_v1_project_is_rejected(tmp_path):
    path = tmp_path / "legacy.ahproject"
    path.write_text(
        """version: 1
name: Legacy
repository: objects.ahobjects
runs_dir: evidence
script_steps:
  - scripts/prepare.ahstep
""",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match="unsupported project version 1"):
        AuthoringProject.load(path)


def test_v2_project_rejects_missing_members(tmp_path):
    path = tmp_path / "project.ahproject"
    path.write_text(
        """version: 2
name: Example
test_plans:
  - missing.ahplan
step_registries: []
object_repositories: []
""",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match="test plan does not exist"):
        AuthoringProject.load(path)
