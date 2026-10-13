"""Explicit, in-memory transfer of plan-local objects into a shared repository.

Callers must persist both resulting documents as a coordinated operation.
No historical object snapshots are retained in the resulting plan.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import os
import tempfile
import yaml
from typing import Mapping

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.models.plan import TestPlan


def reconcile_object(plan: TestPlan, repository: ComponentRepository, source_name: str,
                     *, target_name=None, selection="plan", fields=None):
    """Stage one object migration, returning (updated_plan, updated_repository).

    selection: 'plan', 'repository', or 'combine'. For combine, fields maps
    top-level schema keys to 'plan' or 'repository'. Identity and ownership
    are never silently combined. References are rewritten to the destination
    UUID. The caller must save both outputs together.
    """
    if source_name not in plan.objects:
        raise ComponentRepositoryError("unknown plan-local object %r" % source_name)
    source = plan.objects[source_name]
    if not isinstance(source, Mapping):
        raise ComponentRepositoryError("invalid plan object %r" % source_name)
    source = dict(source)
    source_id = source.get("object_id")
    matches = [item for item in repository.components.values()
               if item.object_id == source_id or item.component_id == (target_name or source_name)]
    if len({item.object_id for item in matches}) > 1:
        raise ComponentRepositoryError("ambiguous repository match for %r" % source_name)
    target = matches[0] if matches else None
    if target_name is not None and target is not None and target.component_id != target_name:
        raise ComponentRepositoryError("target name does not identify the matched object")
    if target is not None and target.object_id != source_id:
        raise ComponentRepositoryError(
            "different immutable identities require an explicit identity mapping before migration"
        )
    if selection not in ("plan", "repository", "combine"):
        raise ValueError("invalid reconciliation selection")
    if target is None and selection != "plan":
        raise ComponentRepositoryError("no repository object exists for selection %r" % selection)
    target_document = repository.to_document()["components"]
    canonical_name = target.component_id if target is not None else (target_name or source_name)
    if target is None and canonical_name in target_document:
        raise ComponentRepositoryError("destination name is occupied")
    if selection == "plan":
        resolved = dict(source)
    elif selection == "repository":
        resolved = dict(target_document[canonical_name])
    else:
        if not isinstance(fields, Mapping):
            raise ValueError("combine requires explicit property selections")
        a = source
        b = target_document[canonical_name]
        unknown = set(fields) - (set(a) | set(b))
        if unknown:
            raise ValueError("unknown properties: %s" % sorted(unknown))
        resolved = {}
        for key in set(a) | set(b):
            if key in ("object_id", "owner_object_id"):
                if a.get(key) != b.get(key):
                    raise ComponentRepositoryError("identity/ownership mismatch at %r" % key)
                resolved[key] = a.get(key)
                continue
            side = fields.get(key)
            if side not in ("plan", "repository"):
                if a.get(key) == b.get(key):
                    side = "plan"
                else:
                    raise ValueError("property %r requires an explicit selection" % key)
            chosen = a if side == "plan" else b
            if key in chosen:
                resolved[key] = chosen[key]
    resolved["object_id"] = source_id
    target_document[canonical_name] = resolved
    updated_repository = ComponentRepository.from_document(
        {"version": 3, "components": target_document}, source="object migration"
    )
    remaining = dict(plan.objects)
    remaining.pop(source_name)
    # A migrated parent cannot leave embedded children pointing at an external
    # owner; migrate the ownership subtree together.
    if any(isinstance(obj, Mapping) and obj.get("owner_object_id") == source_id
           for obj in remaining.values()):
        raise ComponentRepositoryError("migrate child objects with their parent")
    # Existing step references can be UUIDs or component names.
    steps = []
    for call in plan.steps:
        inputs = dict(call.inputs)
        if inputs.get("component_id") in (source_name, source_id):
            inputs["component_id"] = source_id
            call = replace(call, inputs=inputs)
        steps.append(call)
    updated_plan = replace(plan, objects=remaining, steps=tuple(steps))
    return updated_plan, updated_repository


def persist_migration(plan_path: Path, repository_path: Path, plan: TestPlan,
                      repository: ComponentRepository) -> None:
    """Commit a staged migration with rollback on replacement failure.

    Both new documents are validated and written to temporary files before
    either destination changes. Backups exist only during the transaction and
    are removed after successful completion; no snapshots enter plan data.
    """
    from automation_harness.core.test_plan import repository_from_plan
    plan_path = Path(plan_path)
    repository_path = Path(repository_path)
    if plan_path.resolve() == repository_path.resolve():
        raise ValueError("plan and repository paths must differ")
    repository.validate_persistence()
    repository_from_plan(plan)
    from automation_harness.core.object_reconciliation import object_conflicts
    conflicts = object_conflicts(plan.objects, repository)
    if conflicts:
        raise ComponentRepositoryError("; ".join(conflicts))
    payloads = (
        (repository_path, yaml.safe_dump(repository.to_document(), sort_keys=False, allow_unicode=True)),
        (plan_path, yaml.safe_dump(plan.to_dict(), sort_keys=False, allow_unicode=True)),
    )
    prepared = []
    backups = []
    replaced = []
    try:
        for path, payload in payloads:
            if not path.parent.is_dir():
                raise FileNotFoundError("migration destination directory does not exist: %s" % path.parent)
            fd, temp = tempfile.mkstemp(prefix=".%s.migrate." % path.name, dir=str(path.parent))
            prepared.append((path, temp))
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        for path, _temp in prepared:
            if path.exists():
                fd, backup = tempfile.mkstemp(prefix=".%s.rollback." % path.name, dir=str(path.parent))
                os.close(fd)
                with path.open("rb") as source, open(backup, "wb") as dest:
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        dest.write(chunk)
                    dest.flush()
                    os.fsync(dest.fileno())
                backups.append((path, backup))
            else:
                backups.append((path, None))
        for path, temp in prepared:
            os.replace(temp, str(path))
            replaced.append(path)
    except BaseException:
        for path, backup in reversed(backups):
            if path not in replaced:
                continue
            if backup is None:
                path.unlink()
            else:
                os.replace(backup, str(path))
        raise
    finally:
        for _path, temp in prepared:
            if os.path.exists(temp):
                os.unlink(temp)
        for _path, backup in backups:
            if backup is not None and os.path.exists(backup):
                os.unlink(backup)


def detect_legacy_objects(plan: TestPlan, repository: ComponentRepository):
    """Classify embedded definitions without changing either artifact.

    Safe objects are disjoint by name and UUID. Collisions must be resolved
    interactively, including identical duplicate definitions.
    """
    from automation_harness.core.object_reconciliation import object_conflicts
    conflicts = object_conflicts(plan.objects, repository)
    safe = []
    existing_names = set(repository.components)
    existing_ids = {item.object_id for item in repository.components.values()}
    for name, definition in plan.objects.items():
        if not isinstance(definition, Mapping):
            continue
        if name not in existing_names and definition.get("object_id") not in existing_ids:
            safe.append(name)
    return tuple(safe), tuple(conflicts)


def stage_legacy_migration(plan: TestPlan, repository: ComponentRepository):
    """Migrate an entire disjoint legacy object graph without partial changes.

    All conflicts block automatic migration; no A/B choice is inferred.
    Ownership hierarchy remains intact because all embedded definitions move
    together. Step references by name become durable UUID references.
    """
    safe, conflicts = detect_legacy_objects(plan, repository)
    if conflicts:
        raise ComponentRepositoryError("explicit reconciliation required: " + "; ".join(conflicts))
    if len(safe) != len(plan.objects):
        raise ComponentRepositoryError("legacy plan contains invalid embedded object definitions")
    if not safe:
        return plan, repository
    objects = repository.to_document()["components"]
    objects.update(dict(plan.objects))
    merged = ComponentRepository.from_document(
        {"version": 3, "components": objects}, source="legacy migration"
    )
    by_name = {name: definition["object_id"] for name, definition in plan.objects.items()}
    steps = []
    for call in plan.steps:
        inputs = dict(call.inputs)
        reference = inputs.get("component_id")
        if reference in by_name:
            inputs["component_id"] = by_name[reference]
            call = replace(call, inputs=inputs)
        steps.append(call)
    migrated = replace(plan, objects={}, steps=tuple(steps))
    return migrated, merged
