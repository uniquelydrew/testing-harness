"""Authoring project persistence and artifact membership."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.formats import PLAN_SUFFIX, PROJECT_SUFFIX, REPOSITORY_SUFFIX, STEP_REGISTRY_SUFFIX, with_artifact_suffix


class ProjectError(ValueError):
    pass


@dataclass(frozen=True)
class AuthoringProject:
    """Parent container for independently editable authoring artifacts."""

    name: str
    root: Path
    test_plans: tuple[Path, ...] = ()
    step_registries: tuple[Path, ...] = ()
    object_repositories: tuple[Path, ...] = ()

    @classmethod
    def load(cls, path: Path) -> "AuthoringProject":
        path = path.resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise ProjectError("project root must be a mapping")
        root = path.parent
        version = raw.get("version", 1)
        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ProjectError("project requires a non-empty name")

        if version == 1:
            return cls._load_v1(root, name.strip(), raw)
        if version != 2:
            raise ProjectError("unsupported project version %r" % version)

        project = cls(
            name=name.strip(),
            root=root,
            test_plans=_resolve_members(root, raw.get("test_plans", []), "test_plans", PLAN_SUFFIX),
            step_registries=_resolve_members(root, raw.get("step_registries", []), "step_registries", STEP_REGISTRY_SUFFIX),
            object_repositories=_resolve_members(root, raw.get("object_repositories", []), "object_repositories", REPOSITORY_SUFFIX),
        )
        project.validate_members()
        return project

    @classmethod
    def _load_v1(cls, root: Path, name: str, raw: Mapping[str, Any]) -> "AuthoringProject":
        obsolete = [key for key in ("target", "environment_script") if key in raw]
        if obsolete:
            raise ProjectError(
                "obsolete project field(s): %s; application/environment setup belongs in plan steps"
                % ", ".join(obsolete)
            )
        repositories: tuple[Path, ...] = ()
        repository_value = raw.get("repository")
        if repository_value is not None:
            repository = (root / str(repository_value)).resolve()
            repositories = (repository,)
        # v1 runs_dir and script_steps were authoring/runtime configuration, not
        # artifact membership. They are intentionally not persisted in v2.
        return cls(name=name, root=root, object_repositories=repositories)

    def validate_members(self) -> None:
        groups = (
            ("test plan", self.test_plans, PLAN_SUFFIX),
            ("step registry", self.step_registries, STEP_REGISTRY_SUFFIX),
            ("object repository", self.object_repositories, REPOSITORY_SUFFIX),
        )
        for label, paths, suffix in groups:
            normalized: set[Path] = set()
            for path in paths:
                resolved = path.resolve()
                if resolved in normalized:
                    raise ProjectError("duplicate %s membership: %s" % (label, resolved))
                normalized.add(resolved)
                if not resolved.is_file():
                    raise ProjectError("project %s does not exist: %s" % (label, resolved))
                if resolved.suffix.casefold() != suffix.casefold():
                    raise ProjectError("project %s must use %s: %s" % (label, suffix, resolved))

    def to_document(self) -> dict[str, Any]:
        return {
            "version": 2,
            "name": self.name,
            "test_plans": _relative_members(self.root, self.test_plans),
            "step_registries": _relative_members(self.root, self.step_registries),
            "object_repositories": _relative_members(self.root, self.object_repositories),
        }

    def with_test_plan(self, path: Path) -> "AuthoringProject":
        return replace(self, test_plans=_append_member(self.test_plans, path))

    def without_test_plan(self, path: Path) -> "AuthoringProject":
        return replace(self, test_plans=_remove_member(self.test_plans, path))

    def with_step_registry(self, path: Path) -> "AuthoringProject":
        return replace(self, step_registries=_append_member(self.step_registries, path))

    def without_step_registry(self, path: Path) -> "AuthoringProject":
        return replace(self, step_registries=_remove_member(self.step_registries, path))

    def with_object_repository(self, path: Path) -> "AuthoringProject":
        return replace(self, object_repositories=_append_member(self.object_repositories, path))

    def without_object_repository(self, path: Path) -> "AuthoringProject":
        return replace(self, object_repositories=_remove_member(self.object_repositories, path))

    # Transitional compatibility for the existing authoring window. These
    # accessors are intentionally absent from serialization and should disappear
    # once AuthoringApp consumes explicit active artifact state.
    @property
    def repository(self) -> Path:
        if self.object_repositories:
            return self.object_repositories[0]
        return self.root / ("objects" + REPOSITORY_SUFFIX)

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def script_steps(self) -> tuple[Path, ...]:
        return ()

    def load_step_implementations(self) -> None:
        return None


def create_authoring_project(path: Path, name: str) -> AuthoringProject:
    path = with_artifact_suffix(path.resolve(), PROJECT_SUFFIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(name, str) or not name.strip():
        raise ProjectError("project requires a non-empty name")
    project = AuthoringProject(name=name.strip(), root=path.parent)
    save_authoring_project(path, project)
    return project


def save_authoring_project(path: Path, project: AuthoringProject) -> Path:
    path = with_artifact_suffix(path.resolve(), PROJECT_SUFFIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(project.to_document(), sort_keys=False), encoding="utf-8")
    return path


def _resolve_members(root: Path, raw: Any, field: str, suffix: str) -> tuple[Path, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, str) and item.strip() for item in raw):
        raise ProjectError("project %s must be a list of artifact paths" % field)
    result = tuple((root / item).resolve() for item in raw)
    for path in result:
        if path.suffix.casefold() != suffix.casefold():
            raise ProjectError("project %s entries must use %s: %s" % (field, suffix, path))
    return result


def _relative_members(root: Path, paths: tuple[Path, ...]) -> list[str]:
    return [os.path.relpath(str(path), str(root)) for path in paths]


def _append_member(existing: tuple[Path, ...], path: Path) -> tuple[Path, ...]:
    resolved = path.resolve()
    if resolved in existing:
        return existing
    return (*existing, resolved)


def _remove_member(existing: tuple[Path, ...], path: Path) -> tuple[Path, ...]:
    resolved = path.resolve()
    return tuple(item for item in existing if item.resolve() != resolved)
