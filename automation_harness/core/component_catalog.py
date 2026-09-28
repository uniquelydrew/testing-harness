"""Canonical user-facing GUI component catalog.

Framework adapters may expose many implementation classes.  The authoring
surface intentionally uses this small stable vocabulary instead.
"""
from __future__ import annotations

import json
import pkgutil
from functools import lru_cache
from typing import Any, Mapping

from automation_harness.models.gui import ActionType, ObjectType


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


def component_spec(object_type: ObjectType | str) -> Mapping[str, Any]:
    key = object_type.value if isinstance(object_type, ObjectType) else str(object_type)
    types = component_catalog()["types"]
    value = types.get(key) or types.get(ObjectType.CUSTOM.value)
    if not isinstance(value, Mapping):
        raise KeyError("canonical component type %r is not defined" % key)
    return value


def display_name(object_type: ObjectType | str) -> str:
    spec = component_spec(object_type)
    return str(spec.get("display_name") or str(object_type).replace("_", " ").title())


def canonical_action_types(object_type: ObjectType | str) -> frozenset[ActionType]:
    result = set()
    for value in component_spec(object_type).get("actions", ()):
        try:
            result.add(ActionType(str(value)))
        except ValueError:
            raise ValueError(
                "canonical component %r declares unknown action %r" % (object_type, value)
            )
    return frozenset(result)


def is_container(object_type: ObjectType | str) -> bool:
    return bool(component_spec(object_type).get("container"))


def is_fallback_type(object_type: ObjectType | str) -> bool:
    return bool(component_spec(object_type).get("fallback"))
