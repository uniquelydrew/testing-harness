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
    if source.object_id == parent.object_id:
        raise ComponentRepositoryError("an object cannot be its own parent")
    if _is_visual(source) and not _supports_visual_children(parent):
        raise ComponentRepositoryError(
            "visual objects may only be parented by a concrete rendering surface"
        )
    descendants = [
        item.component_id for item in repository.components.values()
        if item.owner_object_id == source.object_id or item.component_id.startswith(old_id + ".")
    ]
    if descendants:
        raise ComponentRepositoryError(
            "only repository leaves can be reparented; %r has %d descendant object(s)" %
            (old_id, len(descendants))
        )
    if source.owner_object_id == parent.object_id:
        return repository, old_id, old_id
    leaf = old_id.rsplit(".", 1)[-1]
    new_id = parent.component_id + "." + leaf
    if new_id != old_id and new_id in repository.components:
        raise ComponentRepositoryError("component %r already exists" % new_id)
    updated = repository.rename(old_id, new_id)
    moved = updated.get(new_id)
    properties = dict(moved.properties or {})
    strategies = moved.strategies
    if _is_visual(source) and source.owner_object_id != parent.object_id:
        # Normalized geometry describes a position in one specific rendering
        # surface. It cannot silently be reinterpreted in another surface.
        properties["locator_status"] = "needs_recapture"
        properties["recapture_reason"] = "rendering_surface_owner_changed"
        properties["previous_owner_object_id"] = source.owner_object_id
    moved = replace(
        moved,
        owner_object_id=parent.object_id,
        revision=source.revision + 1,
        properties=properties,
        strategies=strategies,
    )
    updated = updated.with_component(moved)
    return updated, old_id, new_id


def _is_visual(definition) -> bool:
    return any(strategy.type == "anchored_visual" for strategy in definition.strategies)


def _supports_visual_children(definition) -> bool:
    if str(definition.framework or "").casefold() in {"jogl", "batik", "custom-render"}:
        return True
    boundary = dict(definition.properties or {}).get("capture_boundary")
    return isinstance(boundary, dict) and boundary.get("supports_visual_children") is True
