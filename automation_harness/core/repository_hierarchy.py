"""Concrete Object Repository ownership helpers."""
from __future__ import annotations

from dataclasses import dataclass

from automation_harness.core.component_repository import ComponentRepository


@dataclass(frozen=True)
class RepositoryMigrationReport:
    objects_examined: int
    concrete_ownership_established: int
    synthetic_lineage_segments_ignored: int
    ambiguous_parents: tuple[str, ...]
    visual_objects_needing_recapture: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.ambiguous_parents


def concrete_parent_ids(repository: ComponentRepository) -> dict[str, str | None]:
    """Map every object identity to another real object identity or ``None``.

    Legacy dotted aliases may imply ownership only when the full prefix is an
    actual repository definition. Missing segments are never synthesized.
    """
    result: dict[str, str | None] = {}
    for definition in repository.components.values():
        owner_id = definition.owner_object_id
        if owner_id is None:
            parts = definition.component_id.split(".")
            for depth in range(len(parts) - 1, 0, -1):
                owner = repository.components.get(".".join(parts[:depth]))
                if owner is not None:
                    owner_id = owner.object_id
                    break
        result[definition.object_id] = owner_id
    return result


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
