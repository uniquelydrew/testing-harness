"""Execution of compiled step completion contracts."""
from __future__ import annotations

from typing import Any, Mapping

from automation_harness.core.component_handle import ComponentResolutionError
from automation_harness.core.predicates import compare, evaluate_state
from automation_harness.models.plan import StepCall
from automation_harness.utils.wait import wait_for


class CompletionError(RuntimeError):
    pass


def await_step_completion(
    context,
    call: StepCall,
    resolved_inputs: Mapping[str, Any],
    *,
    signal_baseline: Mapping[str, int] | None = None,
) -> None:
    contract = dict(call.completion)
    mode = contract.get("mode", "automatic")
    if mode == "dispatch-only":
        context.evidence.record("step_completion_skipped", node_id=call.node_id, mode=mode)
        return

    condition = contract.get("condition")
    if mode == "automatic" and condition is None:
        condition = _infer_condition(context, call, resolved_inputs)
        if condition is None:
            context.evidence.record("step_completion_dispatch_only", node_id=call.node_id, mode=mode)
            return
    if mode in {"explicit", "manual"} and not isinstance(condition, Mapping):
        raise CompletionError(f"step {call.node_id!r} completion mode {mode!r} requires a condition")
    if not isinstance(condition, Mapping):
        raise CompletionError(f"step {call.node_id!r} completion condition must be a mapping")

    timeout = _number(contract.get("timeout", 5.0), "timeout", positive=True)
    interval = _number(contract.get("interval", 0.1), "interval", positive=True)
    stability = _number(contract.get("stability_window", 0.0), "stability_window", positive=False)
    try:
        wait_for(
            lambda: _evaluate(context, condition, signal_baseline or {}),
            timeout=timeout,
            interval=interval,
            stability_window=stability,
            description=f"step {call.node_id} completion",
        )
    except TimeoutError as exc:
        context.evidence.record(
            "step_completion_timeout",
            node_id=call.node_id,
            mode=mode,
            condition=dict(condition),
            timeout=timeout,
        )
        raise CompletionError(
            f"step {call.node_id!r} did not complete within {timeout}s; condition={dict(condition)!r}"
        ) from exc
    context.evidence.record(
        "step_completion_satisfied",
        node_id=call.node_id,
        mode=mode,
        condition=dict(condition),
    )


def _infer_condition(context, call: StepCall, inputs: Mapping[str, Any]) -> Mapping[str, Any] | None:
    component_id = inputs.get("component_id")
    if not isinstance(component_id, str) or not context.components.contains(component_id):
        return None
    if call.step_id == "component.text.set" and isinstance(inputs.get("value"), str):
        return {"object": component_id, "property": "text", "equals": inputs["value"]}
    if call.step_id == "component.value.set" and isinstance(inputs.get("value"), (int, float)):
        return {"object": component_id, "property": "value", "equals": inputs["value"]}
    expected = context.components.get(component_id).expected_states
    if expected:
        return {
            "object": component_id,
            "all": [
                {"state": name, "equals": value}
                for name, value in sorted(expected.items())
            ],
        }
    return None


def _evaluate(context, condition: Mapping[str, Any], signal_baseline: Mapping[str, int]) -> bool:
    if "all" in condition and "object" not in condition:
        return all(_evaluate(context, child, signal_baseline) for child in _condition_list(condition, "all"))
    if "any" in condition and "object" not in condition:
        return any(_evaluate(context, child, signal_baseline) for child in _condition_list(condition, "any"))
    if "not" in condition and "object" not in condition:
        child = condition["not"]
        if not isinstance(child, Mapping):
            raise CompletionError("completion not must contain a mapping")
        return not _evaluate(context, child, signal_baseline)
    if "event" in condition:
        event = condition["event"]
        if not isinstance(event, str) or not event:
            raise CompletionError("completion event must be a non-empty string")
        return context.signals.occurred_since(event, signal_baseline)
    if "variable" in condition:
        name = condition["variable"]
        if not isinstance(name, str):
            raise CompletionError("completion variable must be a string")
        actual = context.globals.get(name) if context.globals is not None else None
        operator, expected = _comparison(condition)
        return compare(actual, operator, expected)

    component_id = condition.get("object", condition.get("component"))
    if not isinstance(component_id, str):
        raise CompletionError("completion condition requires object/component, variable, all, any, or not")
    handle = context.component(component_id)
    assertion_id = condition.get("assertion")
    if assertion_id is not None:
        try:
            handle.assert_named(str(assertion_id))
            return True
        except AssertionError:
            return False
    try:
        state = handle.state()
    except ComponentResolutionError:
        state_name = condition.get("state", condition.get("property"))
        expected = condition.get("equals", condition.get("expected", True))
        return state_name in {"absent", "present", "exists"} and expected in {True, False} and (
            (state_name == "absent" and expected is True) or
            (state_name in {"present", "exists"} and expected is False)
        )

    expression = dict(condition)
    expression.pop("object", None)
    expression.pop("component", None)
    if "all" in expression:
        return all(evaluate_state(state, child) for child in _condition_list(expression, "all"))
    if "any" in expression:
        return any(evaluate_state(state, child) for child in _condition_list(expression, "any"))
    if expression.get("state") == "absent":
        expression = {"state": "present", "equals": not bool(expression.get("equals", True))}
    if expression.get("state") == "exists":
        expression["state"] = "present"
    return evaluate_state(state, expression)


def _condition_list(value: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    children = value[key]
    if not isinstance(children, list) or not all(isinstance(child, Mapping) for child in children):
        raise CompletionError(f"completion {key} must be a list of mappings")
    return list(children)


def _comparison(condition: Mapping[str, Any]) -> tuple[str, Any]:
    if "operator" in condition:
        return str(condition["operator"]), condition.get("expected")
    return "equals", condition.get("equals", condition.get("expected", True))


def _number(value: Any, name: str, *, positive: bool) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise CompletionError(f"completion {name} must be numeric")
    result = float(value)
    if (positive and result <= 0) or (not positive and result < 0):
        raise CompletionError(f"completion {name} has an invalid value {value!r}")
    return result
