"""Portable snapshots of reusable Registry Steps and their required objects."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Mapping

import yaml

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.core.test_plan import load_plan
from automation_harness.models.plan import TestPlan


_SNAPSHOT_KIND = "reusable_step"
_SNAPSHOT_VERSION = 1


class ReusableStepSnapshotError(ValueError):
    pass


def snapshot_reusable_dependencies(
    plan: TestPlan,
    definitions: Mapping[str, ReusableStepDefinition],
    repository: ComponentRepository,
) -> TestPlan:
    """Embed only reusable definitions transitively referenced by ``plan``.

    Required component definitions from those reusable compositions are embedded
    into the plan alongside any already embedded plan objects. The authored calls
    remain Registry Step invocations; this is a dependency snapshot, not expansion.
    """
    used = _collect_used_definitions(plan, definitions)
    snapshots = dict(plan.step_definitions)
    for step_id, definition in used.items():
        snapshots[step_id] = reusable_step_to_snapshot(definition)

    objects = dict(plan.objects)
    serialized = repository.to_document().get("components", {})
    for definition in used.values():
        for component_id in _literal_component_refs(definition.plan):
            if not repository.contains(component_id):
                raise ReusableStepSnapshotError(
                    "reusable step %r references unknown component %r" %
                    (definition.step_id, component_id)
                )
            component = repository.get(component_id)
            objects[component.component_id] = serialized[component.component_id]

    return replace(plan, step_definitions=snapshots, objects=objects)


def load_snapshotted_reusable_steps(plan: TestPlan) -> dict[str, ReusableStepDefinition]:
    result = {}
    for step_id, raw in plan.step_definitions.items():
        if not isinstance(raw, Mapping) or raw.get("kind") != _SNAPSHOT_KIND:
            continue
        definition = reusable_step_from_snapshot(raw)
        if definition.step_id != step_id:
            raise ReusableStepSnapshotError(
                "snapshot key %r does not match reusable step id %r" %
                (step_id, definition.step_id)
            )
        result[step_id] = definition
    return result


def reusable_step_to_snapshot(definition: ReusableStepDefinition) -> dict[str, Any]:
    return {
        "kind": _SNAPSHOT_KIND,
        "version": _SNAPSHOT_VERSION,
        "id": definition.step_id,
        "name": definition.name,
        "description": definition.description,
        "inputs": dict(definition.inputs),
        "outputs": dict(definition.outputs),
        "plan": definition.plan.to_dict(),
    }


def reusable_step_from_snapshot(raw: Mapping[str, Any]) -> ReusableStepDefinition:
    if raw.get("kind") != _SNAPSHOT_KIND or raw.get("version") != _SNAPSHOT_VERSION:
        raise ReusableStepSnapshotError("unsupported reusable step snapshot")
    step_id = raw.get("id")
    name = raw.get("name")
    inputs = raw.get("inputs", {})
    outputs = raw.get("outputs", {})
    plan_raw = raw.get("plan")
    if not isinstance(step_id, str) or not step_id:
        raise ReusableStepSnapshotError("reusable step snapshot requires an id")
    if not isinstance(name, str) or not name:
        raise ReusableStepSnapshotError("reusable step snapshot requires a name")
    if not isinstance(inputs, Mapping) or not isinstance(outputs, Mapping):
        raise ReusableStepSnapshotError("reusable step snapshot inputs/outputs must be mappings")
    if not isinstance(plan_raw, Mapping):
        raise ReusableStepSnapshotError("reusable step snapshot requires a plan mapping")

    with NamedTemporaryFile("w+", suffix=".ahplan", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(yaml.safe_dump(dict(plan_raw), sort_keys=False))
    try:
        nested_plan = load_plan(temporary)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return ReusableStepDefinition(
        step_id=step_id,
        name=name,
        description=str(raw.get("description", "")),
        plan=nested_plan,
        inputs=dict(inputs),
        outputs=dict(outputs),
    )


def _collect_used_definitions(
    plan: TestPlan,
    definitions: Mapping[str, ReusableStepDefinition],
) -> dict[str, ReusableStepDefinition]:
    used = {}
    pending = [call.step_id for call in plan.steps if call.step_id in definitions]
    while pending:
        step_id = pending.pop(0)
        if step_id in used:
            continue
        definition = definitions[step_id]
        used[step_id] = definition
        for call in definition.plan.steps:
            if call.step_id in definitions and call.step_id not in used:
                pending.append(call.step_id)
    return used


def _literal_component_refs(plan: TestPlan) -> set[str]:
    return {
        value
        for call in plan.steps
        for value in (call.inputs.get("component_id"),)
        if isinstance(value, str) and value
    }
