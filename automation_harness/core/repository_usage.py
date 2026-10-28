"""Reverse-reference inventory for Project-owned canonical Object Repositories."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from automation_harness.authoring.plan_repository import assigned_repositories
from automation_harness.authoring.step_registry import AuthoringStepRegistry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.repository_scope import RepositoryScope
from automation_harness.core.test_plan import load_plan, repository_from_plan


_REF_KEYS = frozenset({"component_id", "component", "object", "window", "owner_object_id"})


@dataclass(frozen=True)
class ObjectUsage:
    object_id: str
    component_name: str
    artifact: Path
    invocation: str
    source: str
    reference: str


def _refs(value, key=None):
    if key in _REF_KEYS and isinstance(value, str):
        yield value
    if isinstance(value, Mapping):
        for name, item in value.items():
            yield from _refs(item, name)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _refs(item)


def _plan_usages(plan, artifact: Path, target: ComponentRepository, *,
                 linked: bool, source: str):
    snapshot = repository_from_plan(plan)
    shared_by_id = {item.object_id: item for item in target.components.values()}
    for call in plan.steps:
        for value in set(_refs(call.inputs)) | set(_refs(call.scope)) | set(_refs(call.completion)):
            resolved = None
            if snapshot.contains(value):
                candidate = snapshot.get(value)
                resolved = shared_by_id.get(candidate.object_id)
            if resolved is None and linked and target.contains(value):
                resolved = target.get(value)
            if resolved is None:
                continue
            yield ObjectUsage(resolved.object_id, resolved.component_id, artifact,
                              call.node_id, source, value)


def project_object_usages(project, repository_path: Path,
                          repository: ComponentRepository) -> tuple[ObjectUsage, ...]:
    """Find direct plan and inline reusable-step usages within a known Project.

    This is intentionally bounded by explicit Project membership. An object
    may be referenced by artifacts in another Project; those are not
    discoverable without a workspace-wide index.
    """
    repository_path = Path(repository_path).resolve()
    usages = []
    for path in project.test_plans:
        plan = load_plan(path)
        linked = any(
            entry.path.resolve() == repository_path and entry.scope is not RepositoryScope.RECORDING
            for entry in assigned_repositories(plan, path)
        )
        usages.extend(_plan_usages(plan, path, repository, linked=linked, source="test plan"))
    for path in project.step_registries:
        registry = AuthoringStepRegistry.load(path)
        linked = registry.repository.resolve() == repository_path
        for definition in registry.steps:
            usages.extend(_plan_usages(definition.plan, path, repository,
                                       linked=linked, source="step: " + definition.step_id))
    return tuple(sorted(usages, key=lambda item: (
        str(item.artifact), item.source, item.invocation, item.object_id,
    )))
