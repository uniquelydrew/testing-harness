"""Explicit migration from portable plan objects to canonical repository ownership.

An embedded plan is an import source, not a write-through replica. Inspection
never mutates either artifact; collisions need an explicit operator decision.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping

import yaml

from automation_harness.authoring.plan_repository import assign_repositories
from automation_harness.core.component_repository import (
    ComponentRepository, ComponentRepositoryError, RepositoryFingerprint,
    repository_fingerprint,
)
from automation_harness.core.repository_preparation import SNAPSHOT_METADATA_KEY, definition_fingerprint
from automation_harness.core.repository_reconciliation import _atomic_publish, _merge
from automation_harness.core.test_plan import repository_from_plan, validate_plan_components
from automation_harness.core.repository_scope import RepositoryAssociation, RepositoryScope


@dataclass(frozen=True)
class MigrationObject:
    object_id: str
    name: str
    status: str
    canonical_id: str | None
    canonical_name: str | None
    owner_object_id: str | None
    candidates: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationInspection:
    objects: tuple[MigrationObject, ...]
    embedded_count: int
    conflicting_count: int
    embedded_names: tuple[str, ...]


@dataclass(frozen=True)
class MigrationProposal:
    repository: ComponentRepository
    plan: object
    remapped_ids: Mapping[str, str]
    added_ids: tuple[str, ...]
    reused_ids: tuple[str, ...]


def _matching_locator_candidates(definition, canonical: ComponentRepository):
    """Only exact compatible locator contracts suggest duplicate identity."""
    candidates = []
    for current in canonical.components.values():
        if current.object_id == definition.object_id or current.object_type != definition.object_type:
            continue
        if (definition.framework and current.framework and
                definition.framework != current.framework):
            continue
        if (definition.scope and current.scope and definition.scope != current.scope):
            continue
        if any(source == dest for source in definition.strategies for dest in current.strategies):
            candidates.append(current)
    return tuple(candidates)


def inspect_embedded_repository(plan, canonical: ComponentRepository) -> MigrationInspection:
    """Return a non-destructive inventory of exactly the embedded definitions."""
    embedded = repository_from_plan(plan)
    by_uuid = {item.object_id: item for item in canonical.components.values()}
    rows = []
    for item in embedded.components.values():
        current = by_uuid.get(item.object_id)
        name_collision = canonical.components.get(item.component_id)
        locator_candidates = _matching_locator_candidates(item, canonical)
        if current is not None:
            if current.object_type != item.object_type:
                status = "type_conflict"
            elif definition_fingerprint(current) != definition_fingerprint(item):
                status = "canonical_changed"
            else:
                status = "canonical_uuid"
        elif name_collision is not None:
            status = "name_conflict"
            current = name_collision
        elif locator_candidates:
            status = "possible_duplicate"
            current = locator_candidates[0] if len(locator_candidates) == 1 else None
        else:
            status = "new"
        rows.append(MigrationObject(
            item.object_id, item.component_id, status,
            current.object_id if current is not None else None,
            current.component_id if current is not None else None,
            item.owner_object_id,
            tuple(candidate.object_id for candidate in locator_candidates),
        ))
    names = tuple(sorted({item.component_id for item in embedded.components.values()}))
    return MigrationInspection(
        tuple(sorted(rows, key=lambda item: item.name.casefold())),
        len(rows), sum(item.status in {"name_conflict", "type_conflict", "possible_duplicate"} for item in rows), names,
    )


_REFERENCE_KEYS = frozenset({"component_id", "component", "object", "window", "owner_object_id"})


def prepare_embedded_migration(plan, plan_path: Path, canonical: ComponentRepository,
                               repository_path: Path, *,
                               resolutions: Mapping[str, str] | None = None,
                               renames: Mapping[str, str] | None = None,
                               approved_distinct: tuple[str, ...] = ()) -> MigrationProposal:
    """Qualify the entire graph before publication.

    resolutions maps *embedded UUID* to an existing canonical UUID. It is the
    only permitted way to coalesce two distinct identities. Same-name objects
    are not silently considered identical.
    """
    embedded = repository_from_plan(plan)
    mappings = dict(resolutions or {})
    names = dict(renames or {})
    source_ids = {item.object_id for item in embedded.components.values()}
    unknown = (set(mappings) | set(names) | set(approved_distinct)) - source_ids
    if unknown:
        raise ValueError("migration resolution references unknown embedded UUIDs: " + ", ".join(sorted(unknown)))
    for object_id, new_name in names.items():
        if not isinstance(new_name, str) or not new_name.strip():
            raise ValueError("migration object rename requires a non-empty name: %s" % object_id)
        if object_id in mappings:
            raise ValueError("migration cannot both remap and rename an embedded object: %s" % object_id)
    existing_by_id = {item.object_id: item for item in canonical.components.values()}
    remap = {}
    for definition in embedded.components.values():
        selected = mappings.get(definition.object_id, definition.object_id)
        if (definition.object_id in existing_by_id and
                existing_by_id[definition.object_id].object_type != definition.object_type and
                selected == definition.object_id):
            raise ValueError("embedded UUID has conflicting canonical semantic type: %s" % definition.component_id)
        if selected != definition.object_id and selected not in existing_by_id:
            raise ValueError("migration resolution must name an existing canonical UUID: %s" % selected)
        if selected != definition.object_id and existing_by_id[selected].object_type != definition.object_type:
            raise ValueError("cannot coalesce incompatible object types: %s" % definition.component_id)
        remap[definition.object_id] = selected

    # A source identity can be imported without change or explicitly merged
    # into an existing canonical identity. Destination display names are authoritative.
    result = canonical
    added, reused = [], []
    for definition in embedded.components.values():
        dest_id = remap[definition.object_id]
        current = existing_by_id.get(dest_id)
        if (current is None and _matching_locator_candidates(definition, canonical) and
                definition.object_id not in approved_distinct and definition.object_id not in names and
                names.get(definition.object_id, definition.component_id) not in result.components):
            raise ValueError(
                "embedded object %s has an exact locator match in the canonical repository; "
                "explicitly map its UUID or approve importing it as a distinct identity"
                % definition.component_id
            )
        if current is not None:
            reused.append(dest_id)
            if dest_id != definition.object_id:
                # Explicit resolution promotes alternative locator strategies.
                result = result.with_component(_merge(result.get(dest_id), definition))
            continue
        proposed_name = names.get(definition.object_id, definition.component_id)
        if proposed_name in result.components:
            raise ValueError(
                "embedded object %s conflicts with canonical name; explicitly map its UUID to "
                "a compatible existing object or rename the imported object" % definition.component_id
            )
        added.append(dest_id)

    # Build all imported entries with post-reconciliation ownership at once.
    imports = {}
    for definition in embedded.components.values():
        if remap[definition.object_id] in existing_by_id:
            continue
        owner = definition.owner_object_id
        updated = replace(
            definition,
            component_id=names.get(definition.object_id, definition.component_id),
            owner_object_id=remap.get(owner, owner) if owner is not None else None,
        )
        if updated.component_id in imports:
            raise ValueError("migration imports use duplicate name: %s" % updated.component_id)
        imports[updated.component_id] = updated
    result = ComponentRepository({**result.components, **imports})
    result.validate_persistence()

    references = dict(remap)
    references.update({
        definition.component_id: remap[definition.object_id]
        for definition in embedded.components.values()
    })
    # Legacy names on canonical objects also resolve to the surviving UUID.
    references.update({
        definition.component_id: definition.object_id
        for definition in result.components.values()
        if definition.component_id not in references
    })

    def translate(value, key=None):
        if key in _REFERENCE_KEYS and isinstance(value, str):
            return references.get(value, value)
        if isinstance(value, Mapping):
            return {name: translate(item, name) for name, item in value.items()}
        if isinstance(value, (tuple, list)):
            return type(value)(translate(item) for item in value)
        return value

    steps = tuple(replace(
        call, inputs=translate(call.inputs), completion=translate(call.completion),
        scope=translate(call.scope),
    ) for call in plan.steps)
    metadata = dict(plan.step_definitions)
    metadata.pop(SNAPSHOT_METADATA_KEY, None)
    migrated = replace(plan, steps=steps, objects={}, step_definitions=metadata, execution_mode="canonical")
    migrated = assign_repositories(
        migrated, Path(plan_path),
        (RepositoryAssociation(Path(repository_path).resolve(), RepositoryScope.LOCAL),),
    )
    issues = validate_plan_components(migrated, result)
    if issues:
        raise ComponentRepositoryError("migration leaves unresolved plan references: " + "; ".join(issues))
    return MigrationProposal(result, migrated, remap, tuple(added), tuple(sorted(set(reused))))


def publish_embedded_migration(proposal: MigrationProposal, *, repository_path: Path,
                               plan_path: Path,
                               expected_fingerprints: Mapping[Path, RepositoryFingerprint],
                               project_path: Path | None = None,
                               project: object | None = None) -> None:
    """Publish canonical repository and external-only plan as one guarded operation."""
    repo_path = Path(repository_path)
    plan_path = Path(plan_path)
    if repo_path == plan_path:
        raise ValueError("repository and plan destinations must differ")
    proposal.repository.validate_persistence()
    if proposal.plan.objects or proposal.plan.execution_mode != "canonical":
        raise ValueError("migration must produce an external-only canonical plan")
    expected = {Path(path): baseline for path, baseline in expected_fingerprints.items()}
    targets = {
        repo_path: yaml.safe_dump(proposal.repository.to_document(), sort_keys=False, allow_unicode=True),
        plan_path: yaml.safe_dump(proposal.plan.to_dict(), sort_keys=False, allow_unicode=True),
    }
    if (project_path is None) != (project is None):
        raise ValueError("migration requires both project_path and project for a Project update")
    if project_path is not None:
        target = Path(project_path)
        if target in targets:
            raise ValueError("Project destination overlaps plan/repository")
        targets[target] = yaml.safe_dump(project.to_document(), sort_keys=False, allow_unicode=True)
    _atomic_publish(targets, expected_fingerprints=expected)
