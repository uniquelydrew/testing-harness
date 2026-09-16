"""Repository object reparenting while preserving immutable identity."""
from __future__ import annotations

from dataclasses import replace

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError


def reparent_leaf(repository: ComponentRepository, component_id: str, parent_component_id: str):
    """Move one concrete repository leaf beneath another concrete object.

    Returns ``(repository, old_id, new_id)``. Both source and destination must
    be persisted objects; synthetic/logical hierarchy nodes are intentionally
    impossible to pass to this API. The source immutable ``object_id`` is
    preserved and its revision is incremented.
    """
    source = repository.get(component_id)
    parent = repository.get(parent_component_id)
    old_id = source.component_id
    parent_id = parent.component_id
    if source.object_id == parent.object_id:
        raise ComponentRepositoryError("an object cannot be its own parent")
    descendants = [name for name in repository.components if name.startswith(old_id + ".")]
    if descendants:
        raise ComponentRepositoryError(
            "only repository leaves can be reparented; %r has %d descendant object(s)" %
            (old_id, len(descendants))
        )
    leaf = old_id.rsplit(".", 1)[-1]
    new_id = parent_id + "." + leaf
    if new_id == old_id:
        return repository, old_id, new_id
    if new_id in repository.components:
        raise ComponentRepositoryError("component %r already exists" % new_id)
    updated = repository.rename(old_id, new_id)
    moved = updated.get(new_id)
    updated = updated.with_component(replace(moved, revision=moved.revision + 1))
    return updated, old_id, new_id
