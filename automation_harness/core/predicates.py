"""Shared predicate evaluation for assertions and completion conditions."""
from __future__ import annotations

import re
from typing import Any, Mapping


class PredicateError(ValueError):
    pass


def compare(actual: Any, operator: str, expected: Any) -> bool:
    operations = {
        "equals": lambda: actual == expected,
        "not_equals": lambda: actual != expected,
        "contains": lambda: expected in actual,
        "not_contains": lambda: expected not in actual,
        "greater_than": lambda: actual > expected,
        "greater_than_or_equal": lambda: actual >= expected,
        "less_than": lambda: actual < expected,
        "less_than_or_equal": lambda: actual <= expected,
        "matches": lambda: re.search(str(expected), str(actual)) is not None,
    }
    try:
        return bool(operations[operator]())
    except KeyError as exc:
        raise PredicateError(f"unsupported predicate operator {operator!r}") from exc
    except (TypeError, ValueError) as exc:
        raise PredicateError(
            f"cannot evaluate {actual!r} {operator} {expected!r}: {exc}"
        ) from exc


def evaluate_state(state: Any, expression: Mapping[str, Any]) -> bool:
    """Evaluate a normalized expression against one ComponentState."""
    if "all" in expression:
        children = _children(expression, "all")
        return all(evaluate_state(state, child) for child in children)
    if "any" in expression:
        children = _children(expression, "any")
        return any(evaluate_state(state, child) for child in children)
    if "not" in expression:
        child = expression["not"]
        if not isinstance(child, Mapping):
            raise PredicateError("predicate not must contain a mapping")
        return not evaluate_state(state, child)

    state_name = expression.get("state", expression.get("property"))
    if not isinstance(state_name, str) or not state_name:
        raise PredicateError("state predicate requires state or property")
    actual = state.get(state_name)
    if "operator" in expression:
        operator = expression["operator"]
        if not isinstance(operator, str):
            raise PredicateError("predicate operator must be a string")
        expected = expression.get("expected")
    elif "equals" in expression:
        operator, expected = "equals", expression["equals"]
    else:
        operator, expected = "equals", expression.get("expected", True)
    return compare(actual, operator, expected)


def _children(expression: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    value = expression[key]
    if not isinstance(value, list) or not value or not all(isinstance(item, Mapping) for item in value):
        raise PredicateError(f"predicate {key} must contain a non-empty list of mappings")
    return list(value)
