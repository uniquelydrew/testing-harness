"""Detect conflicts between plan-local objects and assigned repository objects.

Reconciliation is an authoring operation, never an implicit execution merge.
"""
from __future__ import annotations

from typing import Mapping

from automation_harness.core.component_repository import ComponentRepository


def object_conflicts(objects: Mapping, repository: ComponentRepository):
    """Return actionable validation messages for overlapping object ownership.

    A plan-local definition is independent of the assigned repository. If its
    name or immutable ID overlaps an OR definition, migration/reconciliation
    must explicitly transfer ownership before execution.
    """
    by_id = {definition.object_id: name for name, definition in repository.components.items()}
    issues = []
    for name, definition in objects.items():
        if not isinstance(definition, Mapping):
            issues.append("plan object %r must be a mapping" % name)
            continue
        object_id = definition.get("object_id")
        name_match = repository.components.get(name)
        id_match = by_id.get(object_id) if isinstance(object_id, str) else None
        if name_match is not None and name_match.object_id != object_id:
            issues.append(
                "object ownership conflict: plan object %r and repository object %r "
                "share a name but have different identities; reconcile before execution"
                % (name, name)
            )
        elif id_match is not None and id_match != name:
            issues.append(
                "object ownership conflict: plan object %r and repository object %r "
                "share immutable object_id %r; reconcile names and transfer ownership"
                % (name, id_match, object_id)
            )
        elif name_match is not None:
            issues.append(
                "object ownership conflict: %r is defined in both the plan and "
                "assigned repository; migrate or remove the embedded definition"
                % name
            )
    return issues
