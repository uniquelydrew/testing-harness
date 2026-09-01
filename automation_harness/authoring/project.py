"""Authoring project configuration for live-desktop test composition."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.core.script_steps import load_script_steps


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class AuthoringProject:
    name: str
    root: Path
    repository: Path
    runs_dir: Path
    script_steps: tuple[Path, ...] = ()

    @classmethod
    def load(cls, path: Path) -> "AuthoringProject":
        path = path.resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise ProjectError("project root must be a mapping")
        obsolete = [key for key in ("target", "environment_script") if key in raw]
        if obsolete:
            raise ProjectError(
                "obsolete project field(s): %s; application/environment setup belongs in plan steps"
                % ", ".join(obsolete)
            )
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
        missing = [str(manifest) for manifest in script_steps if not manifest.is_file()]
        if missing:
            raise ProjectError("project script-step manifest does not exist: " + ", ".join(missing))

        project = cls(
            name=name.strip(),
            root=root,
            repository=repository.resolve(),
            runs_dir=runs_dir.resolve(),
            script_steps=script_steps,
        )
        project.load_step_implementations()
        return project

    def load_step_implementations(self) -> None:
        load_script_steps(self.script_steps)

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


# Import-only shims for the old base GTK module. Live authoring replaces every
# code path that could invoke these names; they deliberately provide no legacy
# application-scope behavior.
class AttachedExecutionBackend:
    def __init__(self, *_args, **_kwargs) -> None:
        raise ProjectError("managed application execution has been removed from authoring")


def applications_for_plan(*_args, **_kwargs):
    return frozenset()


create_reference_project = create_authoring_project
