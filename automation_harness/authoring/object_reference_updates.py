"""Project-wide propagation of Object Repository identity/name changes.

Object ``object_id`` values are immutable; component IDs are authored aliases.
When an alias changes (rename or reparent), every project artifact bound to the
same Object Repository must be rewritten together so persisted reusable steps
and test plans do not retain stale aliases.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import tempfile
from typing import Mapping

import yaml

from automation_harness.authoring.plan_repository import assigned_repository_path
from automation_harness.authoring.project import AuthoringProject
from automation_harness.authoring.step_registry import AuthoringStepRegistry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_identity_sync import rename_plan_component
from automation_harness.core.test_plan import load_plan


@dataclass(frozen=True)
class ReferenceUpdate:
    path: Path
    artifact_type: str
    replacements: int


@dataclass(frozen=True)
class ReferenceUpdateReport:
    repository: Path
    renames: Mapping[str, str]
    updates: tuple[ReferenceUpdate, ...] = ()

    @property
    def changed_files(self) -> tuple[Path, ...]:
        return tuple(item.path for item in self.updates)

    @property
    def replacement_count(self) -> int:
        return sum(item.replacements for item in self.updates)


def normalize_rename_map(rename_map: Mapping[str, str]) -> dict[str, str]:
    """Normalize composed aliases so each source maps directly to its final ID.

    Multiple historic aliases may legitimately converge on the same current
    alias (for example A->B followed by B->C), so convergence is not itself a
    collision. Repository validation remains responsible for rejecting two live
    objects that would occupy the same component ID.
    """
    result = {}
    for source, target in rename_map.items():
        source = str(source).strip()
        target = str(target).strip()
        if not source or not target or source == target:
            continue
        seen = {source}
        while target in rename_map and target not in seen:
            seen.add(target)
            next_target = str(rename_map[target]).strip()
            if not next_target or next_target == target:
                break
            target = next_target
        if target in seen:
            raise ValueError("object rename map contains a cycle involving %r" % source)
        result[source] = target
    return result


def preview_project_reference_updates(project_path: Path, repository_path: Path, rename_map: Mapping[str, str]) -> ReferenceUpdateReport:
    """Build and validate all dependent artifact rewrites without writing files."""
    project_path = Path(project_path).resolve()
    repository_path = Path(repository_path).resolve()
    renames = normalize_rename_map(rename_map)
    if not renames:
        return ReferenceUpdateReport(repository_path, {})
    project = AuthoringProject.load(project_path)
    updates = []

    for path in project.test_plans:
        plan = load_plan(path)
        assigned = assigned_repository_path(plan, path)
        if assigned is None or assigned.resolve() != repository_path:
            continue
        rewritten, count = _rewrite_plan(plan, renames)
        if count:
            updates.append((path.resolve(), "test_plan", count, rewritten.to_dict()))

    for path in project.step_registries:
        registry = AuthoringStepRegistry.load(path)
        if registry.repository.resolve() != repository_path:
            continue
        rewritten_steps = []
        count = 0
        for step in registry.steps:
            rewritten_plan, replacements = _rewrite_plan(step.plan, renames)
            rewritten_steps.append(replace(step, plan=rewritten_plan))
            count += replacements
        if count:
            rewritten = replace(registry, steps=tuple(rewritten_steps))
            updates.append((path.resolve(), "step_registry", count, rewritten.to_document()))

    for _path, _kind, _count, document in updates:
        yaml.safe_dump(document, sort_keys=False, allow_unicode=True)

    return ReferenceUpdateReport(
        repository=repository_path,
        renames=renames,
        updates=tuple(ReferenceUpdate(path, kind, count) for path, kind, count, _document in updates),
    )


def apply_project_reference_updates(
    project_path: Path,
    repository_path: Path,
    rename_map: Mapping[str, str],
    *,
    repository: ComponentRepository | None = None,
) -> ReferenceUpdateReport:
    """Atomically persist a repository mutation and all dependent alias rewrites.

    Files are fully staged and fsynced first. Commit uses same-directory
    ``os.replace`` operations. If any replace fails, prior bytes are restored for
    every file already committed.
    """
    project_path = Path(project_path).resolve()
    repository_path = Path(repository_path).resolve()
    renames = normalize_rename_map(rename_map)
    project = AuthoringProject.load(project_path)
    staged = []
    report_updates = []

    if repository is not None:
        staged.append((repository_path, repository.to_document()))
        report_updates.append(ReferenceUpdate(repository_path, "object_repository", 0))

    for path in project.test_plans:
        plan = load_plan(path)
        assigned = assigned_repository_path(plan, path)
        if assigned is None or assigned.resolve() != repository_path:
            continue
        rewritten, count = _rewrite_plan(plan, renames)
        if count:
            staged.append((path.resolve(), rewritten.to_dict()))
            report_updates.append(ReferenceUpdate(path.resolve(), "test_plan", count))

    for path in project.step_registries:
        registry = AuthoringStepRegistry.load(path)
        if registry.repository.resolve() != repository_path:
            continue
        rewritten_steps = []
        count = 0
        for step in registry.steps:
            rewritten_plan, replacements = _rewrite_plan(step.plan, renames)
            rewritten_steps.append(replace(step, plan=rewritten_plan))
            count += replacements
        if count:
            rewritten = replace(registry, steps=tuple(rewritten_steps))
            staged.append((path.resolve(), rewritten.to_document()))
            report_updates.append(ReferenceUpdate(path.resolve(), "step_registry", count))

    _atomic_write_documents(staged)
    return ReferenceUpdateReport(repository_path, renames, tuple(report_updates))


def _rewrite_plan(plan, renames):
    current = plan
    replacements = 0
    for old, new in renames.items():
        before = current.to_dict()
        current = rename_plan_component(current, old, new)
        after = current.to_dict()
        replacements += _count_changed_scalars(before, after)
    return current, replacements


def _count_changed_scalars(before, after):
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        return sum(_count_changed_scalars(before.get(key), after.get(key)) for key in set(before) | set(after))
    if isinstance(before, (list, tuple)) and isinstance(after, (list, tuple)):
        size = max(len(before), len(after))
        return sum(_count_changed_scalars(before[index] if index < len(before) else None, after[index] if index < len(after) else None) for index in range(size))
    return 0 if before == after else 1


def _atomic_write_documents(staged):
    if not staged:
        return
    originals = {}
    temporary_paths = []
    committed = []
    try:
        for path, document in staged:
            path = Path(path).resolve()
            originals[path] = path.read_bytes() if path.exists() else None
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = yaml.safe_dump(document, sort_keys=False, allow_unicode=True).encode("utf-8")
            fd, temporary_name = tempfile.mkstemp(prefix=".%s.update-" % path.name, dir=str(path.parent))
            temporary = Path(temporary_name)
            temporary_paths.append(temporary)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(str(temporary), str(path))
            committed.append(path)
            temporary_paths.remove(temporary)
    except Exception:
        for path in reversed(committed):
            original = originals[path]
            if original is None:
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            fd, recovery_name = tempfile.mkstemp(prefix=".%s.rollback-" % path.name, dir=str(path.parent))
            with os.fdopen(fd, "wb") as handle:
                handle.write(original)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(recovery_name, str(path))
        raise
    finally:
        for temporary in temporary_paths:
            try:
                temporary.unlink()
            except OSError:
                pass
