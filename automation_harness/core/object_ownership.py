"""Derive an effective JavaFX owning window from canonical object lineage.

This module never rewrites repository definitions or substitutes a different
object identity. It only identifies the Stage in which the target must live.
"""
from __future__ import annotations

from typing import Mapping


class ObjectOwnershipError(ValueError):
    pass


def owning_window_hint(definition, repository=None):
    """Return an explicit JavaFX Stage title, if supported by durable evidence.

    The component's own scope wins (dialog and popup controls may belong to a
    different window from their parent's). If absent, inherit from the nearest
    canonical owner by immutable object ID. Window evidence on an owner can
    reside in either scope.ownership.window or its JavaFX locator.
    """
    window = _window_from_scope(definition)
    if window is not None:
        return window
    if repository is None or not getattr(definition, "owner_object_id", None):
        return None
    visited = {definition.object_id}
    owner_id = definition.owner_object_id
    while owner_id:
        if owner_id in visited:
            raise ObjectOwnershipError("cyclic canonical owner_object_id lineage")
        visited.add(owner_id)
        if not repository.contains(owner_id):
            raise ObjectOwnershipError(
                "canonical owner %r is absent for %r"
                % (owner_id, definition.component_id)
            )
        parent = repository.get(owner_id)
        window = _window_from_scope(parent) or _window_from_javafx_locator(parent)
        if window is not None:
            return window
        owner_id = parent.owner_object_id
    return None


def _window_from_scope(definition):
    scope = getattr(definition, "scope", None)
    if not isinstance(scope, Mapping):
        return None
    ownership = scope.get("ownership")
    if not isinstance(ownership, Mapping):
        return None
    window = ownership.get("window")
    return window.strip() if isinstance(window, str) and window.strip() else None


def _window_from_javafx_locator(definition):
    for strategy in getattr(definition, "strategies", ()):
        if strategy.type != "javafx":
            continue
        identity = strategy.options.get("identification")
        if not isinstance(identity, Mapping):
            continue
        candidates = []
        for source in (identity.get("mandatory"), identity.get("assistive"), identity):
            if isinstance(source, Mapping):
                window = source.get("window")
                if isinstance(window, str) and window.strip():
                    candidates.append(window.strip())
        if len(set(candidates)) > 1:
            raise ObjectOwnershipError(
                "conflicting JavaFX window evidence on canonical owner %r"
                % definition.component_id
            )
        if candidates:
            return candidates[0]
    return None
