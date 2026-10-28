"""Consistent object-reference rewriting across plans and reusable-step snapshots."""
from __future__ import annotations

from dataclasses import replace
from typing import Mapping

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.models.plan import TestPlan

_REFERENCE_KEYS = frozenset({"component_id", "component", "object", "window", "owner_object_id"})
_SNAPSHOT_KEY = "__repository_snapshot__"


def rewrite_object_references(value, references, key=None):
    """Rewrite *only* semantic reference fields; preserve opaque user strings."""
    if isinstance(value, str) and key in _REFERENCE_KEYS:
        return references.get(value, value)
    if isinstance(value, Mapping):
        return {k: rewrite_object_references(v, references, k) for k, v in value.items()}
    if isinstance(value, list):
        return [rewrite_object_references(v, references) for v in value]
    if isinstance(value, tuple):
        return tuple(rewrite_object_references(v, references) for v in value)
    return value


def _snapshot_objects(steps, repository, definitions=None):
    """Build ownership-closed embedded definitions from serialized step calls."""
    referenced = set()
    def collect(value, key=None):
        if key in _REFERENCE_KEYS and isinstance(value, str):
            # Only component references are checked; 'window' may be a title.
            if key in ("component_id", "component", "object"):
                referenced.add(value)
        if isinstance(value, Mapping):
            for k, item in value.items():
                collect(item, k)
        elif isinstance(value, (tuple, list)):
            for item in value:
                collect(item)
    for step in steps:
        if isinstance(step, Mapping):
            for k in ("inputs", "scope", "completion"):
                collect(step.get(k, {}))
    # Portable execution resolves against the plan's top-level object closure
    # even after reusable steps are expanded. Collect their transitive calls.
    for raw in (definitions or {}).values():
        if not isinstance(raw, Mapping) or raw.get("kind") != "reusable_step":
            continue
        inner = raw.get("plan", {})
        if not isinstance(inner, Mapping):
            raise ValueError("reusable-step snapshot plan must be a mapping")
        nested = inner.get("steps", [])
        if not isinstance(nested, list):
            raise ValueError("reusable-step snapshot steps must be a list")
        for call in nested:
            if isinstance(call, Mapping):
                for key in ("inputs", "scope", "completion"):
                    collect(call.get(key, {}))
        nested_definitions = inner.get("step_definitions", {})
        if isinstance(nested_definitions, Mapping):
            # Recursion is over finite serialized snapshot documents, not
            # execution calls, and does not need the nested closure itself.
            for nested_object in _snapshot_objects(nested, repository, nested_definitions).values():
                referenced.add(nested_object["object_id"])
    embedded = {}
    serial = repository.to_document()["components"]
    visited = set()
    def include(reference):
        if not repository.contains(reference):
            raise ComponentRepositoryError("reusable snapshot references missing canonical object %r" % reference)
        definition = repository.get(reference)
        if definition.object_id in visited:
            return
        visited.add(definition.object_id)
        if definition.owner_object_id:
            include(definition.owner_object_id)
        embedded[definition.component_id] = serial[definition.component_id]
    for reference in sorted(referenced):
        include(reference)
    return embedded


def _rewrite_snapshots(definitions, references, repository, *, canonical_only):
    revised = {}
    for step_id, raw in definitions.items():
        if step_id == _SNAPSHOT_KEY:
            continue  # Stale fingerprint provenance must never survive a rewrite.
        if not isinstance(raw, Mapping) or raw.get("kind") != "reusable_step":
            revised[step_id] = raw
            continue
        snapshot = dict(raw)
        inner = snapshot.get("plan")
        if not isinstance(inner, Mapping):
            raise ValueError("reusable-step snapshot %r has no plan mapping" % step_id)
        inner = dict(inner)
        steps = inner.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("reusable-step snapshot %r has invalid steps" % step_id)
        inner["steps"] = [rewrite_object_references(item, references) for item in steps]
        nested = inner.get("step_definitions", {})
        if not isinstance(nested, Mapping):
            raise ValueError("reusable-step snapshot %r has invalid step definitions" % step_id)
        inner["step_definitions"] = _rewrite_snapshots(
            nested, references, repository, canonical_only=canonical_only,
        )
        if canonical_only:
            inner["objects"] = {}
            inner["execution_mode"] = "canonical"
        else:
            inner["objects"] = _snapshot_objects(inner["steps"], repository, inner["step_definitions"])
            # Embedded snapshot provenance must match the rebuilt objects.
            from automation_harness.core.repository_preparation import snapshot_provenance
            embedded = ComponentRepository.from_document({
                "version": 3, "components": inner["objects"],
            })
            inner["step_definitions"][_SNAPSHOT_KEY] = {
                "version": 1, "objects": snapshot_provenance(embedded),
            }
        snapshot["plan"] = inner
        revised[step_id] = snapshot
    return revised


def rewrite_plan_object_references(plan: TestPlan, references: Mapping[str, str],
                                   repository: ComponentRepository) -> TestPlan:
    """Update nested step plans and preserve canonical vs portable ownership."""
    calls = tuple(replace(
        call,
        inputs=rewrite_object_references(call.inputs, references),
        scope=rewrite_object_references(call.scope, references),
        completion=rewrite_object_references(call.completion, references),
    ) for call in plan.steps)
    canonical_only = plan.execution_mode == "canonical"
    definitions = _rewrite_snapshots(
        plan.step_definitions, references, repository, canonical_only=canonical_only,
    )
    revised = replace(plan, steps=calls, step_definitions=definitions,
                      objects={} if canonical_only else plan.objects)
    if canonical_only:
        return revised
    # Rebuild a complete portable closure, including nested reusable steps.
    # embed_plan_repository() alone includes direct calls, not snapshot calls.
    serialized_steps = [call.to_dict() for call in calls]
    embedded_objects = _snapshot_objects(serialized_steps, repository, definitions)
    from automation_harness.core.repository_preparation import snapshot_provenance
    embedded_repo = ComponentRepository.from_document({
        "version": 3, "components": embedded_objects,
    })
    metadata = dict(definitions)
    metadata[_SNAPSHOT_KEY] = {
        "version": 1, "objects": snapshot_provenance(embedded_repo),
    }
    return replace(revised, objects=embedded_objects, step_definitions=metadata)


def validate_rewritten_plan_references(plan: TestPlan, repository: ComponentRepository) -> None:
    """Validate direct and expanded inline reusable-step object references."""
    from automation_harness.core.reusable_step_snapshot import load_snapshotted_reusable_steps
    from automation_harness.core.reusable_step_expansion import expand_reusable_steps
    from automation_harness.core.test_plan import validate_plan_components
    definitions = load_snapshotted_reusable_steps(plan)
    expanded = expand_reusable_steps(plan, definitions) if definitions else plan
    issues = validate_plan_components(expanded, repository)
    if issues:
        raise ComponentRepositoryError(
            "reconciled plan has unresolved object references: " + "; ".join(issues)
        )
