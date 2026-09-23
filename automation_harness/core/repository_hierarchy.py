"""Concrete Object Repository ownership helpers."""
from __future__ import annotations

from automation_harness.core.component_repository import ComponentRepository


def concrete_parent_ids(repository: ComponentRepository) -> dict[str, str | None]:
    """Map every object identity to another real object identity or ``None``.

    Ownership is always explicit and identity-based.
    """
    result: dict[str, str | None] = {}
    for definition in repository.components.values():
        result[definition.object_id] = definition.owner_object_id
    return result



def is_authoring_visible(definition) -> bool:
    return str(
        dict(definition.properties or {}).get("authoring_visibility") or "visible"
    ) != "structural_only"


def authoring_parent_ids(repository: ComponentRepository) -> dict[str, str | None]:
    """Return semantic authoring parents with structural-only nodes skipped."""
    concrete = concrete_parent_ids(repository)
    by_id = {item.object_id: item for item in repository.components.values()}
    result: dict[str, str | None] = {}
    for definition in repository.components.values():
        if not is_authoring_visible(definition):
            continue
        owner = concrete.get(definition.object_id)
        visited = set()
        while owner is not None and owner in by_id and not is_authoring_visible(by_id[owner]):
            if owner in visited:
                owner = None
                break
            visited.add(owner)
            owner = concrete.get(owner)
        result[definition.object_id] = owner if owner in by_id else None
    return result


def visible_authoring_definitions(repository: ComponentRepository):
    return tuple(
        item for item in repository.components.values()
        if is_authoring_visible(item)
    )

def repository_migration_report(repository: ComponentRepository) -> RepositoryMigrationReport:
    """Report legacy hierarchy normalization without guessing relationships."""
    established = 0
    ignored = 0
    ambiguous = []
    recapture = []
    for definition in repository.components.values():
        if dict(definition.properties or {}).get("locator_status") == "needs_recapture":
            recapture.append(definition.component_id)
        if definition.owner_object_id is not None:
            established += 1
            continue
        parts = definition.component_id.split(".")
        concrete = []
        for depth in range(1, len(parts)):
            candidate = repository.components.get(".".join(parts[:depth]))
            if candidate is not None:
                concrete.append(candidate)
        missing = max(0, len(parts) - 1 - len(concrete))
        ignored += missing
        if concrete:
            established += 1
        # More than one equally-qualified concrete parent should never be
        # possible in a mapping. Keep this explicit for imported/corrupt data.
        longest = [item for item in concrete if len(item.component_id.split(".")) == max(
            (len(value.component_id.split(".")) for value in concrete), default=0,
        )]
        if len(longest) > 1:
            ambiguous.append(definition.component_id)
    return RepositoryMigrationReport(
        objects_examined=len(repository.components),
        concrete_ownership_established=established,
        synthetic_lineage_segments_ignored=ignored,
        ambiguous_parents=tuple(sorted(ambiguous)),
        visual_objects_needing_recapture=tuple(sorted(recapture)),
    )
