"""Compile user-authored reusable Registry Steps into executable TestPlan calls."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan


class ReusableStepExpansionError(ValueError):
    pass


def expand_reusable_steps(
    plan: TestPlan,
    definitions: Mapping[str, ReusableStepDefinition],
) -> TestPlan:
    """Expand Registry Step invocations while preserving the authored plan boundary.

    Calls whose ``step_id`` is not present in ``definitions`` remain untouched. Reusable
    calls are recursively expanded, internal nodes and variables are namespaced by the
    invocation node id, and downstream explicit dependencies on the invocation are
    redirected to the terminal expanded nodes.
    """
    expanded, replacements = _expand_calls(plan.steps, definitions, stack=())
    rewritten = tuple(
        replace(call, depends_on=_rewrite_dependencies(call.depends_on, replacements))
        for call in expanded
    )
    return replace(plan, steps=rewritten)


def _expand_calls(
    calls: tuple[StepCall, ...],
    definitions: Mapping[str, ReusableStepDefinition],
    *,
    stack: tuple[str, ...],
) -> tuple[tuple[StepCall, ...], dict[str, tuple[str, ...]]]:
    result: list[StepCall] = []
    replacements: dict[str, tuple[str, ...]] = {}

    for call in calls:
        definition = definitions.get(call.step_id)
        if definition is None:
            result.append(call)
            continue
        if definition.step_id in stack:
            cycle = " -> ".join((*stack, definition.step_id))
            raise ReusableStepExpansionError("reusable step cycle: " + cycle)

        inner = _instantiate_definition(call, definition)
        recursively_expanded, inner_replacements = _expand_calls(
            inner,
            definitions,
            stack=(*stack, definition.step_id),
        )
        recursively_expanded = tuple(
            replace(
                item,
                depends_on=_rewrite_dependencies(item.depends_on, inner_replacements),
            )
            for item in recursively_expanded
        )
        terminals = _terminal_nodes(recursively_expanded)
        if not terminals:
            raise ReusableStepExpansionError(
                "reusable step %r expands to no executable calls" % definition.step_id
            )
        result.extend(recursively_expanded)
        replacements[call.node_id] = terminals

    return tuple(result), replacements


def _instantiate_definition(
    invocation: StepCall,
    definition: ReusableStepDefinition,
) -> tuple[StepCall, ...]:
    contract = dict(definition.inputs)
    unknown = sorted(set(invocation.inputs) - set(contract))
    if unknown:
        raise ReusableStepExpansionError(
            "reusable step %r has no input(s): %s" % (definition.step_id, ", ".join(unknown))
        )

    input_values: dict[str, Any] = {}
    for name, metadata in contract.items():
        if name in invocation.inputs:
            input_values[name] = invocation.inputs[name]
            continue
        required = bool(metadata.get("required", False))
        if required and "default" not in metadata:
            raise ReusableStepExpansionError(
                "reusable step %r missing required input %r" % (definition.step_id, name)
            )
        if "default" in metadata:
            input_values[name] = metadata["default"]

    output_contract = dict(definition.outputs)
    unknown_outputs = sorted(set(invocation.outputs) - set(output_contract))
    if unknown_outputs:
        raise ReusableStepExpansionError(
            "reusable step %r has no output(s): %s" % (definition.step_id, ", ".join(unknown_outputs))
        )

    node_map = {
        inner.node_id: _namespace(invocation.node_id, inner.node_id)
        for inner in definition.plan.steps
    }
    produced_variables = {
        variable
        for inner in definition.plan.steps
        for variable in inner.outputs.values()
        if variable
    }
    variable_map = {
        variable: _namespace(invocation.node_id, variable)
        for variable in produced_variables
    }
    for output_name, selector in output_contract.items():
        if output_name in invocation.outputs:
            variable_map[str(selector)] = invocation.outputs[output_name]

    instantiated: list[StepCall] = []
    for inner in definition.plan.steps:
        dependencies = tuple(node_map.get(dep, dep) for dep in inner.depends_on)
        if not dependencies:
            dependencies = invocation.depends_on
        instantiated.append(
            replace(
                inner,
                node_id=node_map[inner.node_id],
                inputs=_substitute_value(inner.inputs, input_values, variable_map, invocation.node_id),
                outputs={
                    name: variable_map.get(variable, _namespace(invocation.node_id, variable))
                    for name, variable in inner.outputs.items()
                },
                depends_on=dependencies,
                completion=_substitute_value(inner.completion, input_values, variable_map, invocation.node_id),
                scope=_substitute_value(inner.scope, input_values, variable_map, invocation.node_id),
                group=invocation.group or inner.group,
            )
        )
    return tuple(instantiated)


def _substitute_value(
    value: Any,
    inputs: Mapping[str, Any],
    variables: Mapping[str, str],
    namespace: str,
) -> Any:
    if isinstance(value, PlanVariableRef):
        root, separator, suffix = value.path.partition(".")
        if root in inputs:
            supplied = inputs[root]
            if not separator:
                return supplied
            if isinstance(supplied, PlanVariableRef):
                return PlanVariableRef(supplied.path + "." + suffix)
            return _select_nested(supplied, suffix, root=root)
        mapped = variables.get(root)
        if mapped is not None:
            return PlanVariableRef(mapped + (("." + suffix) if separator else ""))
        return PlanVariableRef(_namespace(namespace, value.path))
    if isinstance(value, dict):
        return {key: _substitute_value(item, inputs, variables, namespace) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute_value(item, inputs, variables, namespace) for item in value]
    if isinstance(value, tuple):
        return tuple(_substitute_value(item, inputs, variables, namespace) for item in value)
    return value


def _select_nested(value: Any, suffix: str, *, root: str) -> Any:
    current = value
    for part in suffix.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
            continue
        try:
            current = getattr(current, part)
        except AttributeError as exc:
            raise ReusableStepExpansionError(
                "reusable input %r does not contain nested path %r" % (root, suffix)
            ) from exc
    return current


def _rewrite_dependencies(
    dependencies: tuple[str, ...],
    replacements: Mapping[str, tuple[str, ...]],
) -> tuple[str, ...]:
    rewritten: list[str] = []
    for dependency in dependencies:
        values = replacements.get(dependency, (dependency,))
        for value in values:
            if value not in rewritten:
                rewritten.append(value)
    return tuple(rewritten)


def _terminal_nodes(calls: tuple[StepCall, ...]) -> tuple[str, ...]:
    depended_on = {dependency for call in calls for dependency in call.depends_on}
    return tuple(call.node_id for call in calls if call.node_id not in depended_on)


def _namespace(prefix: str, value: str) -> str:
    return "%s::%s" % (prefix, value)
