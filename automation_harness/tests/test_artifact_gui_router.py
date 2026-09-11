from pathlib import Path

import pytest

from automation_harness.authoring.gui.router import ArtifactType, detect_artifact


def test_detects_each_first_class_artifact_by_schema_and_suffix(tmp_path):
    project = tmp_path / "suite.ahproject"
    project.write_text("version: 2\nname: Suite\ntest_plans: []\nstep_registries: []\nobject_repositories: []\n", encoding="utf-8")
    plan = tmp_path / "login.ahplan"
    plan.write_text("version: 1\nname: Login\nvariables: {}\nobjects: {}\nstep_definitions: {}\nsteps: []\n", encoding="utf-8")
    registry = tmp_path / "common.ahregistry"
    registry.write_text("version: 1\nname: Common\nobject_repository: common.ahobjects\nsteps: []\n", encoding="utf-8")
    repository = tmp_path / "common.ahobjects"
    repository.write_text("version: 3\ncomponents: {}\n", encoding="utf-8")

    assert detect_artifact(project) is ArtifactType.PROJECT
    assert detect_artifact(plan) is ArtifactType.TEST_PLAN
    assert detect_artifact(registry) is ArtifactType.STEP_REGISTRY
    assert detect_artifact(repository) is ArtifactType.OBJECT_REPOSITORY


def test_detects_generic_yaml_by_document_schema(tmp_path):
    path = tmp_path / "portable.yaml"
    path.write_text("version: 1\nname: Portable\nvariables: {}\nobjects: {}\nstep_definitions: {}\nsteps: []\n", encoding="utf-8")
    assert detect_artifact(path) is ArtifactType.TEST_PLAN


def test_rejects_suffix_schema_mismatch(tmp_path):
    path = tmp_path / "not-a-plan.ahplan"
    path.write_text("version: 3\ncomponents: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="extension"):
        detect_artifact(path)


def test_supports_legacy_project_schema(tmp_path):
    path = tmp_path / "legacy.yaml"
    path.write_text("version: 1\nname: Legacy\nrepository: objects.ahobjects\n", encoding="utf-8")
    assert detect_artifact(path) is ArtifactType.PROJECT
