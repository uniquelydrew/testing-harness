"""Canonical user-facing GUI component catalog with opt-in extension profiles.

Base lookups retain their current behavior; registered profiles require an
explicit registry and never mutate canonical type specifications.
"""
from __future__ import annotations

import json
import pkgutil
from functools import lru_cache
from typing import Any, Mapping, TYPE_CHECKING

from automation_harness.models.gui import ActionType, ObjectType

if TYPE_CHECKING:
    from automation_harness.extensions.registry import ExtensionRegistry


@lru_cache(maxsize=1)
def component_catalog() -> Mapping[str, Any]:
    raw = pkgutil.get_data("automation_harness", "resources/component_types.json")
    if raw is None:
        raise RuntimeError("canonical component catalog resource is unavailable")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, Mapping) or value.get("version") != 1:
        raise ValueError("canonical component catalog requires version 1")
    types = value.get("types")
    if not isinstance(types, Mapping):
        raise ValueError("canonical component catalog requires a types mapping")
    return value


def component_spec(
    object_type: ObjectType | str, *, registry: ExtensionRegistry | None = None,
) -> Mapping[str, Any]:
    key = object_type.value if isinstance(object_type, ObjectType) else str(object_type)
    types = component_catalog()["types"]
    if key in types:
        value = types[key]
    elif "." in key:
        if registry is None:
            raise ValueError("extension type %r requires an explicit extension registry" % key)
        profile = registry.get_component(key)
        base = types[profile["extends"]]
        value = {
            **dict(base),
            "id": key,
            "base_type": profile["extends"],
            "display_name": profile.get("display_name", base.get("display_name")),
            "capabilities": tuple(profile.get("capabilities", ())),
            "interaction": dict(profile.get("interaction", {})),
            "observation": dict(profile.get("observation", {})),
            "fields": tuple(profile.get("fields", ())),
        }
        if value["interaction"].get("selection") is False:
            value["actions"] = []  # status popups are not selectable menus
        elif "actions" in profile:
            value["actions"] = tuple(profile["actions"])
    else:
        value = types.get(ObjectType.CUSTOM.value)
    if not isinstance(value, Mapping):
        raise KeyError("canonical component type %r is not defined" % key)
    return value


def display_name(object_type: ObjectType | str, *, registry: ExtensionRegistry | None = None) -> str:
    spec = component_spec(object_type, registry=registry)
    return str(spec.get("display_name") or str(object_type).replace("_", " ").title())


def canonical_action_types(
    object_type: ObjectType | str, *, registry: ExtensionRegistry | None = None,
) -> frozenset[ActionType]:
    result = set()
    for value in component_spec(object_type, registry=registry).get("actions", ()):
        try:
            result.add(ActionType(str(value)))
        except ValueError:
            raise ValueError(
                "canonical component %r declares unknown action %r" % (object_type, value)
            )
    return frozenset(result)


def is_container(object_type: ObjectType | str, *, registry: ExtensionRegistry | None = None) -> bool:
    return bool(component_spec(object_type, registry=registry).get("container"))


def is_fallback_type(object_type: ObjectType | str, *, registry: ExtensionRegistry | None = None) -> bool:
    return bool(component_spec(object_type, registry=registry).get("fallback"))
