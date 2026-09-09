from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Mapping

import yaml

from automation_harness.formats import PLAN_SUFFIX, PROJECT_SUFFIX, REPOSITORY_SUFFIX, STEP_REGISTRY_SUFFIX


class ArtifactType(str, Enum):
    PROJECT = "project"
    TEST_PLAN = "test_plan"
    STEP_REGISTRY = "step_registry"
    OBJECT_REPOSITORY = "object_repository"


_SUFFIX_TYPES = {
    PROJECT_SUFFIX.casefold(): ArtifactType.PROJECT,
    PLAN_SUFFIX.casefold(): ArtifactType.TEST_PLAN,
    STEP_REGISTRY_SUFFIX.casefold(): ArtifactType.STEP_REGISTRY,
    REPOSITORY_SUFFIX.casefold(): ArtifactType.OBJECT_REPOSITORY,
}


def detect_artifact(path: Path) -> ArtifactType:
    path = Path(path)
    suffix_type = _SUFFIX_TYPES.get(path.suffix.casefold())
    raw = _load_mapping(path)
    schema_type = _detect_schema(raw)
    if suffix_type is not None and schema_type is not None and suffix_type != schema_type:
        raise ValueError(
            "%s uses the %s extension but its document schema is %s" %
            (path, suffix_type.value, schema_type.value)
        )
    if suffix_type is not None:
        return suffix_type
    if schema_type is not None:
        return schema_type
    raise ValueError("unable to determine Automation Harness artifact type for %s" % path)


def _load_mapping(path: Path) -> Mapping:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ValueError("unable to read artifact %s: %s" % (path, exc)) from exc
    if not isinstance(raw, Mapping):
        raise ValueError("artifact root must be a mapping: %s" % path)
    return raw


def _detect_schema(raw: Mapping):
    if "test_plans" in raw or "step_registries" in raw or "object_repositories" in raw:
        return ArtifactType.PROJECT
    if "object_repository" in raw and "steps" in raw:
        return ArtifactType.STEP_REGISTRY
    if "components" in raw:
        return ArtifactType.OBJECT_REPOSITORY
    if "steps" in raw and "name" in raw and ("variables" in raw or "objects" in raw or "step_definitions" in raw):
        return ArtifactType.TEST_PLAN
    if raw.get("version") == 1 and "repository" in raw and "name" in raw:
        return ArtifactType.PROJECT
    return None


def open_window(path: Path, *, project_context=None):
    artifact_type = detect_artifact(path)
    if artifact_type is ArtifactType.PROJECT:
        from automation_harness.authoring.gui.project_window import ProjectWindow
        return ProjectWindow(path, opener=open_window).finish_build()
    if artifact_type is ArtifactType.TEST_PLAN:
        from automation_harness.authoring.gui.plan_recording_window import RecordingTestPlanWindow
        return RecordingTestPlanWindow(path, project_context=project_context, opener=open_window).finish_build()
    if artifact_type is ArtifactType.STEP_REGISTRY:
        from automation_harness.authoring.gui.registry_window import StepRegistryWindow
        return StepRegistryWindow(path, project_context=project_context, opener=open_window).finish_build()
    from automation_harness.authoring.gui.repository_window import ObjectRepositoryWindow
    return ObjectRepositoryWindow(path, project_context=project_context, opener=open_window).finish_build()
