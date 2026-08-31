"""Authoring project configuration and environment setup."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.backends.attached_desktop import AttachedDesktopBackend
from automation_harness.backends.base import ExecutionBackend
from automation_harness.backends.java_desktop import JavaDesktopBackend
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.backends.reference import ReferenceBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.plan import TestPlan
from automation_harness.models.run import BackendHealth


class ProjectError(ValueError):
    pass


class AttachedExecutionBackend(ExecutionBackend):
    """Compatibility adapter for a backend launched outside plan execution."""

    def __init__(self, backend: ExecutionBackend, environment: Mapping[str, str]) -> None:
        self.backend = backend
        self.environment = dict(environment)
        self.name = backend.name

    @property
    def capabilities(self):
        return self.backend.capabilities

    @property
    def allowed_step_risks(self):
        return self.backend.allowed_step_risks

    def preflight_issues(self):
        return [] if self.backend.health_check().healthy else ["attached environment is not healthy"]

    def start(self, *, run_dir: Path):
        if not self.backend.health_check().healthy:
            raise RuntimeError("attached environment is no longer healthy")
        return dict(self.environment)

    def health_check(self) -> BackendHealth:
        return self.backend.health_check()

    def stop(self) -> None:
        return None


@dataclass(frozen=True)
class AuthoringProject:
    name: str
    root: Path
    repository: Path
    runs_dir: Path
    # Retained only so older project manifests remain readable. New projects do
    # not persist or require a target application/backend selection.
    target: Mapping[str, Any] = field(default_factory=dict)
    environment_script: Path | None = None

    @classmethod
    def load(cls, path: Path) -> "AuthoringProject":
        path = path.resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise ProjectError("project root must be a mapping")
        root = path.parent
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProjectError("project requires a non-empty name")
        target = raw.get("target", {})
        if not isinstance(target, Mapping):
            raise ProjectError("legacy project.target must be a mapping")
        repository = root / str(raw.get("repository", "components.yaml"))
        runs_dir = root / str(raw.get("runs_dir", "runs"))
        script_value = raw.get("environment_script")
        script = root / str(script_value) if script_value else None
        return cls(
            name.strip(),
            root,
            repository.resolve(),
            runs_dir.resolve(),
            dict(target),
            script.resolve() if script else None,
        )

    def backend(self):
        """Return an execution backend, honoring only explicit legacy targets.

        New projects default to the current live desktop. Environment startup is
        handled by ``prepare_environment`` before non-authoring execution.
        """
        kind = self.target.get("kind")
        if not kind:
            return LiveDesktopBackend()
        display = str(self.target.get("display", "native"))
        if kind == "reference":
            return ReferenceBackend(gui=bool(self.target.get("gui", True)), display_mode=display)
        if kind == "java-desktop":
            return JavaDesktopBackend(self.target, display_mode=display)
        if kind == "attached-desktop":
            expected = self.target.get("expected_application")
            return AttachedDesktopBackend(self.target) if expected else LiveDesktopBackend()
        raise ProjectError("unsupported legacy authoring target kind %r" % kind)

    def prepare_environment(self) -> None:
        """Run the project's startup script for external/non-authoring runs."""
        if self.environment_script is None:
            return
        if not self.environment_script.is_file():
            raise ProjectError("environment script does not exist: %s" % self.environment_script)
        result = subprocess.run(
            [str(self.environment_script)],
            cwd=str(self.root),
            env=os.environ.copy(),
            check=False,
        )
        if result.returncode:
            raise ProjectError("environment script failed with exit code %d" % result.returncode)

    def to_document(self) -> dict[str, Any]:
        value = {
            "version": 1,
            "name": self.name,
            "repository": os.path.relpath(str(self.repository), str(self.root)),
            "runs_dir": os.path.relpath(str(self.runs_dir), str(self.root)),
        }
        if self.environment_script is not None:
            value["environment_script"] = os.path.relpath(
                str(self.environment_script), str(self.root)
            )
        return value


def create_authoring_project(path: Path, name: str) -> AuthoringProject:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    project = AuthoringProject(
        name,
        path.parent,
        path.parent / "components.yaml",
        path.parent / "runs",
        {},
    )
    project.repository.parent.mkdir(parents=True, exist_ok=True)
    if not project.repository.exists():
        project.repository.write_text("version: 2\ncomponents: {}\n", encoding="utf-8")
    path.write_text(yaml.safe_dump(project.to_document(), sort_keys=False), encoding="utf-8")
    return project


# Compatibility for callers from the initial project implementation.
create_reference_project = create_authoring_project


def applications_for_plan(plan: TestPlan, repository: ComponentRepository) -> frozenset[str]:
    """Legacy diagnostic helper; application lineage now remains object-local."""
    applications: set[str] = set()
    for call in plan.steps:
        component_id = call.inputs.get("component_id")
        if not isinstance(component_id, str) or not repository.contains(component_id):
            continue
        definition = repository.get(component_id)
        for strategy in definition.strategies:
            identity = strategy.options.get("identification")
            if not isinstance(identity, dict):
                continue
            if "mandatory" in identity:
                application = (identity.get("mandatory") or {}).get("application") or (
                    identity.get("assistive") or {}
                ).get("application")
            else:
                application = identity.get("application")
            if isinstance(application, str):
                applications.add(application)
    return frozenset(applications)
