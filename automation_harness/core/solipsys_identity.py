"""Shared identity policy for semantic objects rendered inside Solipsys canvases.

Coordinates and Java runtime references intentionally do not appear here.  A
Solipsys locator identifies a domain object; geometry is resolved state.
"""
from __future__ import annotations

from typing import Any, Mapping


FRAMEWORK = "solipsys_rendered"
STRATEGY = "java_agent"
SEMANTIC_KEYS = (
    "rendered_class",
    "track_class",
    "track_identity_key",
    "track_identity_value",
)
SCOPE_KEYS = ("native_class", "accessible_id", "window", "application")
RUNTIME_KEYS = frozenset({
    "bounds", "coordinates", "position", "ref", "rendered_object_ref",
    "surface_ref", "runtime_ref", "selected", "selection_state",
})


def strategy_parts(options: Mapping[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(options, Mapping):
        return {}, {}
    raw = options.get("identification", options)
    if not isinstance(raw, Mapping):
        return {}, {}
    mandatory = raw.get("mandatory", raw)
    assistive = raw.get("assistive", {})
    return (
        dict(mandatory) if isinstance(mandatory, Mapping) else {},
        dict(assistive) if isinstance(assistive, Mapping) else {},
    )


def locator_is_complete(mandatory: Mapping[str, Any]) -> bool:
    return all(mandatory.get(key) not in (None, "") for key in SEMANTIC_KEYS)


def semantic_identity(mandatory: Mapping[str, Any]) -> tuple[str, str, str, str] | None:
    if not locator_is_complete(mandatory):
        return None
    return tuple(str(mandatory[key]) for key in SEMANTIC_KEYS)  # type: ignore[return-value]


def scope_compatible(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    """Require agreement for scope values present on both sides.

    Scope narrows which canvas owns an object, but missing optional scope data
    never turns geometry or a runtime reference into identity evidence.
    """
    for key in SCOPE_KEYS:
        lvalue, rvalue = left.get(key), right.get(key)
        if lvalue not in (None, "") and rvalue not in (None, ""):
            if str(lvalue).casefold() != str(rvalue).casefold():
                return False
    return True


def locators_match(
    left_mandatory: Mapping[str, Any],
    left_assistive: Mapping[str, Any],
    right_mandatory: Mapping[str, Any],
    right_assistive: Mapping[str, Any],
) -> bool:
    left = semantic_identity(left_mandatory)
    right = semantic_identity(right_mandatory)
    return left is not None and left == right and scope_compatible(left_assistive, right_assistive)

