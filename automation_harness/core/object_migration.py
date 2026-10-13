"""Explicit, in-memory transfer of plan-local objects into a shared repository.

Callers must persist both resulting documents as a coordinated operation.
No historical object snapshots are retained in the resulting plan.
"""
from __future__ import annotations

from dataclasses import replace
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
