from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Iterable
from uuid import UUID

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.models.component import ComponentDefinition


OVERRIDE_PROPERTY = "repository_override_of"


class RepositoryScope(str, Enum):
    LOCAL = "local"
    SHARED = "shared"


@dataclass(frozen=True)
class RepositoryAssociation:
    path: Path
    scope: RepositoryScope


class RepositoryCompositionError(ComponentRepositoryError):
    pass


@dataclass(frozen=True)
class RepositorySet:
    """An ordered set of repositories with explicit local override semantics.

    Shared repositories are applied in declaration order. They may not shadow
    one another by name or immutable ID. A local object replaces a shared
    object only when ``properties.repository_override_of`` names that object's
    UUID. Merely reusing its display name is an error.
    """

    associations: tuple[RepositoryAssociation, ...]
    repositories: tuple[ComponentRepository, ...]

    def __post_init__(self) -> None:
        if len(self.associations) != len(self.repositories):
            raise RepositoryCompositionError("each repository association requires one loaded repository")
        local_count = sum(item.scope is RepositoryScope.LOCAL for item in self.associations)
        if local_count > 1:
            raise RepositoryCompositionError("a repository set may contain at most one local repository")

    @classmethod
    def load(cls, associations: Iterable[RepositoryAssociation]) -> "RepositorySet":
        items = tuple(associations)
        repositories = tuple(
            ComponentRepository.load((item.path,)) if item.path.is_file() else ComponentRepository({})
            for item in items
        )
        return cls(items, repositories)

    def compose(self) -> ComponentRepository:
        by_id: dict[str, ComponentDefinition] = {}
        names: dict[str, str] = {}

        ordered = sorted(
            zip(self.associations, self.repositories),
            key=lambda item: item[0].scope is RepositoryScope.LOCAL,
        )
        for association, repository in ordered:
            for definition in repository.components.values():
                if association.scope is RepositoryScope.LOCAL:
                    override_id = _override_id(definition)
                    if override_id is not None:
                        target = by_id.get(override_id)
                        if target is None:
                            raise RepositoryCompositionError(
                                f"local object {definition.component_id!r} overrides unknown object id {override_id!r}"
                            )
                        names.pop(target.component_id, None)
                        replacement = replace(definition, object_id=override_id)
                        collision = names.get(replacement.component_id)
                        if collision is not None and collision != override_id:
                            raise RepositoryCompositionError(
                                f"local override name {replacement.component_id!r} conflicts with another object"
                            )
                        by_id[override_id] = replacement
                        names[replacement.component_id] = override_id
                        continue
                _insert_strict(by_id, names, definition, association)

        return ComponentRepository({item.component_id: item for item in by_id.values()})


def _insert_strict(
    by_id: dict[str, ComponentDefinition],
    names: dict[str, str],
    definition: ComponentDefinition,
    association: RepositoryAssociation,
) -> None:
    if definition.object_id in by_id:
        previous = by_id[definition.object_id]
        raise RepositoryCompositionError(
            f"{association.scope.value} repository object {definition.component_id!r} duplicates immutable "
            f"object id used by {previous.component_id!r}; declare an explicit local override"
        )
    previous_id = names.get(definition.component_id)
    if previous_id is not None:
        raise RepositoryCompositionError(
            f"display name {definition.component_id!r} identifies different objects ({previous_id}, "
            f"{definition.object_id}); rename one object or declare an explicit local override"
        )
    by_id[definition.object_id] = definition
    names[definition.component_id] = definition.object_id


def _override_id(definition: ComponentDefinition) -> str | None:
    value = definition.properties.get(OVERRIDE_PROPERTY)
    if value is None:
        return None
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise RepositoryCompositionError(
            f"local object {definition.component_id!r} has invalid {OVERRIDE_PROPERTY} UUID"
        ) from exc
