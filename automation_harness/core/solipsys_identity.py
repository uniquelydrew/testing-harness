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
    "surface_ref", "runtime_ref", "track_runtime_ref", "selected", "selection_state",
})
DISQUALIFIED_IDENTITY_KEYS = frozenset({"field:identity"})


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
    return (
        all(mandatory.get(key) not in (None, "") for key in SEMANTIC_KEYS)
        and str(mandatory.get("track_identity_key")) not in DISQUALIFIED_IDENTITY_KEYS
    )


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


def visible_identity_status(properties: Mapping[str, Any] | None) -> str:
    """Classify provisional identity evidence without declaring it durable."""
    if not isinstance(properties, Mapping):
        return "unverified"
    matches = properties.get("identity_visible_match_count")
    unique = properties.get("identity_unique_in_visible_scope")
    if unique is False or (
        isinstance(matches, int) and not isinstance(matches, bool) and matches > 1
    ):
        return "ambiguous"
    if unique is True and matches == 1:
        return "candidate_unique"
    if isinstance(matches, int) and not isinstance(matches, bool) and matches == 0:
        return "unavailable"
    return "unverified"


def runtime_correlation_key(capture) -> tuple[str, str, str] | None:
    """Return process-local evidence for recording correlation only."""
    properties = getattr(capture, "backend_properties", {}) or {}
    runtime_ref = properties.get("track_runtime_ref") or properties.get("rendered_object_ref")
    scope = getattr(capture, "window", None) or getattr(capture, "application", None)
    if runtime_ref in (None, "") or scope in (None, ""):
        return None
    native_class = getattr(capture, "native_class", None) or properties.get("rendered_class") or ""
    return str(scope).casefold(), str(native_class), str(runtime_ref)
