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


_OPEN_PROJECT_WINDOWS = {}


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


def _project_key(path: Path) -> Path:
    return Path(path).resolve()


def _present_existing_project(path: Path):
    key = _project_key(path)
    existing = _OPEN_PROJECT_WINDOWS.get(key)
    if existing is None:
        return None
    window = getattr(existing, "window", None)
    if window is None:
        _OPEN_PROJECT_WINDOWS.pop(key, None)
        return None
    try:
        window.deiconify()
        window.present()
    except Exception:
        _OPEN_PROJECT_WINDOWS.pop(key, None)
        return None
    return existing


def _register_project_window(path: Path, project_window):
    key = _project_key(path)
    _OPEN_PROJECT_WINDOWS[key] = project_window

    def unregister(*_args):
        if _OPEN_PROJECT_WINDOWS.get(key) is project_window:
            _OPEN_PROJECT_WINDOWS.pop(key, None)

    project_window.window.connect("destroy", unregister)
    return project_window


def _finish(window, launching_window):
    window.launching_window = launching_window
    return window.finish_build()


def open_window(path: Path, *, project_context=None, launching_window=None):
    path = Path(path).resolve()
    artifact_type = detect_artifact(path)
    if artifact_type is ArtifactType.PROJECT:
        existing = _present_existing_project(path)
        if existing is not None:
            return existing
        from automation_harness.authoring.gui.project_window import ProjectWindow
        project_window = _finish(ProjectWindow(path, opener=open_window), launching_window)
        return _register_project_window(path, project_window)
    if artifact_type is ArtifactType.TEST_PLAN:
        from automation_harness.authoring.gui.plan_launch_window import LaunchRestoringTestPlanWindow
        return _finish(LaunchRestoringTestPlanWindow(path, project_context=project_context, opener=open_window), launching_window)
    if artifact_type is ArtifactType.STEP_REGISTRY:
        from automation_harness.authoring.gui.registry_window import StepRegistryWindow
        return _finish(StepRegistryWindow(path, project_context=project_context, opener=open_window), launching_window)
    from automation_harness.authoring.gui.repository_workbench_window import WorkbenchObjectRepositoryWindow
    return _finish(WorkbenchObjectRepositoryWindow(path, project_context=project_context, opener=open_window), launching_window)
