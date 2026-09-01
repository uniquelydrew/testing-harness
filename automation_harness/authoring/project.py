"""Authoring project configuration for targetless live-desktop tests."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.backends.base import ExecutionBackend
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.script_steps import load_script_steps
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
    script_steps: tuple[Path, ...] = ()
    # Read-only compatibility payload for old manifests and old authoring code.
    # It is not persisted and never scopes execution or object resolution.
    target: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    # Read-only compatibility path. It is never executed outside the plan.
    environment_script: Path | None = field(default=None, repr=False, compare=False)

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
        repository = root / str(raw.get("repository", "components.yaml"))
        runs_dir = root / str(raw.get("runs_dir", "runs"))

        script_step_values = raw.get("script_steps", [])
        if not isinstance(script_step_values, list) or not all(isinstance(item, str) for item in script_step_values):
            raise ProjectError("project script_steps must be a list of manifest paths")
        script_steps = tuple((root / value).resolve() for value in script_step_values)

        legacy_target = raw.get("target", {})
        if not isinstance(legacy_target, Mapping):
            raise ProjectError("legacy project.target must be a mapping")
        legacy_script_value = raw.get("environment_script")
        legacy_script = (root / str(legacy_script_value)).resolve() if legacy_script_value else None

        project = cls(
            name=name.strip(),
            root=root,
            repository=repository.resolve(),
            runs_dir=runs_dir.resolve(),
            script_steps=script_steps,
            target=dict(legacy_target),
            environment_script=legacy_script,
        )
        project.load_step_implementations()
        return project

    def backend(self):
        """Return the targetless desktop execution facility.

        A project no longer chooses an application or application-specific
        backend. Object ownership is resolved from each object's locator.
        """
        return LiveDesktopBackend()

    def load_step_implementations(self) -> None:
        load_script_steps(self.script_steps)

    def prepare_environment(self) -> None:
        """Reject the obsolete out-of-plan environment startup mechanism."""
        if self.environment_script is not None:
            raise ProjectError(
                "project.environment_script is obsolete and is not executed; "
                "register it as a contract-backed script step and place that step in the test flow"
            )

    def to_document(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "version": 1,
            "name": self.name,
            "repository": os.path.relpath(str(self.repository), str(self.root)),
            "runs_dir": os.path.relpath(str(self.runs_dir), str(self.root)),
        }
        if self.script_steps:
            value["script_steps"] = [
                os.path.relpath(str(path), str(self.root)) for path in self.script_steps
            ]
        return value


def create_authoring_project(path: Path, name: str) -> AuthoringProject:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    project = AuthoringProject(
        name=name,
        root=path.parent,
        repository=path.parent / "components.yaml",
        runs_dir=path.parent / "runs",
    )
    project.repository.parent.mkdir(parents=True, exist_ok=True)
    if not project.repository.exists():
        project.repository.write_text("version: 2\ncomponents: {}\n", encoding="utf-8")
    path.write_text(yaml.safe_dump(project.to_document(), sort_keys=False), encoding="utf-8")
    return project


# Compatibility for callers from the initial project implementation.
create_reference_project = create_authoring_project


def applications_for_plan(plan: TestPlan, repository: ComponentRepository) -> frozenset[str]:
    """Legacy diagnostic helper; application lineage remains object-local."""
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
