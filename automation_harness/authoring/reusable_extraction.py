"""Extract reusable compositions from Test Plans into authoring Step Registries."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from automation_harness.authoring.step_registry import (
    AuthoringStepRegistry,
    create_step_registry,
    save_step_registry,
)
from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan


class ReusableExtractionError(ValueError):
    pass


def extract_reusable_step(
    plan: TestPlan,
    *,
    step_id: str,
    name: str,
    node_ids: Iterable[str] | None = None,
    group: str | None = None,
    description: str = "",
) -> ReusableStepDefinition:
    """Extract selected calls into a reusable mini-plan with an inferred IO contract.

    Exactly one selection mode is accepted: explicit ``node_ids`` or a plan ``group``.
    Internal dependencies are preserved; dependencies on calls outside the extraction
    boundary are removed because they express caller sequencing rather than reusable
    behavior. Plan variable references entering the boundary become inputs, and values
    produced inside the boundary and consumed outside become outputs.
    """
    selected = _select_calls(plan, node_ids=node_ids, group=group)
    selected_ids = {call.node_id for call in selected}

    produced_inside = {
        variable
        for call in selected
        for variable in call.outputs.values()
        if isinstance(variable, str) and variable
    }
    referenced_inside = {
        path
        for call in selected
        for path in _call_variable_refs(call)
    }
    input_roots = sorted({path.split(".", 1)[0] for path in referenced_inside} - produced_inside)

    outside = tuple(call for call in plan.steps if call.node_id not in selected_ids)
    referenced_outside = {
        path
        for call in outside
        for path in _call_variable_refs(call)
    }
    output_roots = sorted(
        variable for variable in produced_inside
        if any(path == variable or path.startswith(variable + ".") for path in referenced_outside)
    )

    extracted_calls = tuple(
        replace(
            call,
            depends_on=tuple(dep for dep in call.depends_on if dep in selected_ids),
            group="",
        )
        for call in selected
    )

    defaults = {
        root: plan.variables[root]
        for root in input_roots
        if root in plan.variables
    }
    inputs = {
        root: _input_contract(root, plan.variables)
        for root in input_roots
    }
    outputs = {root: root for root in output_roots}

    mini_plan = TestPlan(
        name=name.strip() or step_id,
        variables=defaults,
        steps=extracted_calls,
        objects={},
        step_definitions=dict(plan.step_definitions),
    )
    return ReusableStepDefinition(
        step_id=step_id,
        name=name,
        description=description,
        plan=mini_plan,
        inputs=inputs,
        outputs=outputs,
    )


def reconcile_registry_objects(
    definition: ReusableStepDefinition,
    *,
    source_repository: ComponentRepository,
    target_repository: ComponentRepository,
) -> ComponentRepository:
    """Copy literal component references required by a reusable step into its registry repository.

    Existing equivalent identities are reused. Name collisions with different immutable
    object IDs are rejected instead of silently mutating identity.
    """
    result = target_repository
    for component_id in sorted(_literal_component_refs(definition.plan.steps)):
        if not source_repository.contains(component_id):
            raise ReusableExtractionError(
                "reusable step references unknown source component %r" % component_id
            )
        source = source_repository.get(component_id)
        if result.contains(source.component_id):
            existing = result.get(source.component_id)
            if existing.object_id != source.object_id:
                raise ReusableExtractionError(
                    "component %r conflicts with registry repository immutable object id" % source.component_id
                )
            continue
        try:
            result = result.with_component(source)
        except ComponentRepositoryError as exc:
            raise ReusableExtractionError(str(exc)) from exc
    return result


def add_extracted_step_to_registry(
    registry: AuthoringStepRegistry,
    definition: ReusableStepDefinition,
    *,
    source_repository: ComponentRepository,
) -> tuple[AuthoringStepRegistry, ComponentRepository]:
    """Return updated registry metadata and its reconciled repository."""
    target = ComponentRepository.load((registry.repository,))
    reconciled = reconcile_registry_objects(
        definition,
        source_repository=source_repository,
        target_repository=target,
    )
    return registry.with_step(definition), reconciled


def save_plan_selection_to_registry(
    plan: TestPlan,
    *,
    source_repository: ComponentRepository,
    registry_path: Path,
    step_id: str,
    name: str,
    node_ids: Iterable[str] | None = None,
    group: str | None = None,
    description: str = "",
    create_registry_name: str | None = None,
    repository: Path | None = None,
) -> AuthoringStepRegistry:
    """Persist one selected plan composition into an existing or newly created registry.

    ``create_registry_name`` is required only when ``registry_path`` does not yet exist.
    New registries inherit the normal rule that an empty sibling Object Repository is
    created automatically unless ``repository`` is explicitly supplied.
    """
    registry_path = registry_path.resolve()
    if registry_path.is_file():
        registry = AuthoringStepRegistry.load(registry_path)
    else:
        if not create_registry_name:
            raise ReusableExtractionError(
                "step registry does not exist; create_registry_name is required to create it"
            )
        registry = create_step_registry(
            registry_path,
            create_registry_name,
            repository=repository,
        )

    definition = extract_reusable_step(
        plan,
        step_id=step_id,
        name=name,
        node_ids=node_ids,
        group=group,
        description=description,
    )
    updated, reconciled_repository = add_extracted_step_to_registry(
        registry,
        definition,
        source_repository=source_repository,
    )

    # Persist the repository before the registry document so a registry on disk never
    # references object definitions that have not yet been committed to its repository.
    reconciled_repository.save(updated.repository)
    save_step_registry(registry_path, updated)
    return updated


def _select_calls(
    plan: TestPlan,
    *,
    node_ids: Iterable[str] | None,
    group: str | None,
) -> tuple[StepCall, ...]:
    if node_ids is not None and group is not None:
        raise ReusableExtractionError("select reusable calls by node_ids or group, not both")
    if node_ids is None and group is None:
        raise ReusableExtractionError("reusable extraction requires node_ids or group")

    if group is not None:
        selected = tuple(call for call in plan.steps if call.group == group)
        if not selected:
            raise ReusableExtractionError("test plan group %r contains no calls" % group)
        return selected

    requested = tuple(node_ids or ())
    if not requested:
        raise ReusableExtractionError("reusable extraction requires at least one node id")
    requested_set = set(requested)
    known = {call.node_id for call in plan.steps}
    missing = sorted(requested_set - known)
    if missing:
        raise ReusableExtractionError("unknown test plan node id(s): %s" % ", ".join(missing))
    return tuple(call for call in plan.steps if call.node_id in requested_set)


def _input_contract(root: str, variables: Any) -> dict[str, Any]:
    if root not in variables:
        return {"type": "any", "required": True}
    default = variables[root]
    return {
        "type": _value_type_name(default),
        "required": False,
        "default": default,
    }


def _value_type_name(value: Any) -> str:
    if value is None:
        return "any"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return type(value).__name__


def _call_variable_refs(call: StepCall) -> set[str]:
    return (
        _variable_refs(call.inputs)
        | _variable_refs(call.scope)
        | _variable_refs(call.completion)
    )


def _variable_refs(value: Any) -> set[str]:
    if isinstance(value, PlanVariableRef):
        return {value.path}
    if isinstance(value, dict):
        result: set[str] = set()
        for item in value.values():
            result |= _variable_refs(item)
        return result
    if isinstance(value, (list, tuple)):
        result: set[str] = set()
        for item in value:
            result |= _variable_refs(item)
        return result
    return set()


def _literal_component_refs(calls: Iterable[StepCall]) -> set[str]:
    return {
        component_id
        for call in calls
        for component_id in (call.inputs.get("component_id"),)
        if isinstance(component_id, str) and component_id
    }
