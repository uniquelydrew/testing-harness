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
