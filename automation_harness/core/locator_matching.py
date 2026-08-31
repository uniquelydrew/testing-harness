from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


def _matches_value(actual: Any, expected: Any, *, case_insensitive: bool = False) -> bool:
    """Match an identity value exactly or by a full regular expression.

    Regex values are represented as ``{"regex": "..."}``. Full-match
    semantics are intentional: object properties remain predicates over the
    complete runtime value instead of silently becoming substring searches.
    """
    if isinstance(expected, Mapping) and set(expected) == {"regex"}:
        pattern = expected.get("regex")
        if not isinstance(pattern, str) or not pattern:
            return False
        flags = re.IGNORECASE if case_insensitive else 0
        return actual is not None and re.fullmatch(pattern, str(actual), flags=flags) is not None
    if case_insensitive:
        return actual is not None and str(actual).casefold() == str(expected).casefold()
    return actual == expected


def _validate_match_value(error_type, prefix: str, value: Any, *, allow_empty: bool = False) -> None:
    if isinstance(value, str):
        if value or allow_empty:
            return
        raise error_type(f"{prefix} must be a non-empty string")
    if isinstance(value, Mapping) and set(value) == {"regex"}:
        pattern = value.get("regex")
        if not isinstance(pattern, str) or not pattern:
            raise error_type(f"{prefix}.regex must be a non-empty string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise error_type(f"{prefix}.regex is invalid: {exc}") from exc
        return
    raise error_type(f"{prefix} must be a string or {{regex: pattern}}")


def install() -> None:
    """Install regex-aware locator behavior into existing repository/drivers."""
    from automation_harness.core import component_repository
    from automation_harness.drivers import atspi_driver, java_accessibility

    def validate_locator_conditions(prefix: str, conditions: Mapping[str, Any]) -> None:
        for key, value in conditions.items():
            if key in component_repository._ATSPI_SIMPLE_KEYS:
                if key == "hierarchy":
                    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                        raise component_repository.ComponentRepositoryError(
                            f"{prefix}.hierarchy must be a non-empty list of strings"
                        )
                else:
                    _validate_match_value(component_repository.ComponentRepositoryError, f"{prefix}.{key}", value)
                continue
            if key == "parent":
                if not isinstance(value, Mapping) or not value:
                    raise component_repository.ComponentRepositoryError(
                        f"{prefix}.parent must be a non-empty mapping"
                    )
                unknown = set(value) - component_repository._ATSPI_PARENT_KEYS
                if unknown:
                    raise component_repository.ComponentRepositoryError(
                        f"{prefix}.parent contains unsupported properties: {', '.join(sorted(unknown))}"
                    )
                for parent_key, parent_value in value.items():
                    _validate_match_value(
                        component_repository.ComponentRepositoryError,
                        f"{prefix}.parent.{parent_key}",
                        parent_value,
                    )
                continue
            if key.startswith("attribute:") and len(key) > len("attribute:"):
                _validate_match_value(
                    component_repository.ComponentRepositoryError,
                    f"{prefix}.{key}",
                    value,
                    allow_empty=True,
                )
                continue
            raise component_repository.ComponentRepositoryError(
                f"{prefix} contains unsupported AT-SPI property {key!r}"
            )

    def atspi_matches_condition(node: Any, key: str, expected: Any) -> bool:
        if key == "name":
            return _matches_value(getattr(node, "name", None), expected)
        if key == "role":
            return _matches_value(atspi_driver._role_name(node), expected, case_insensitive=True)
        if key == "accessible_id":
            return _matches_value(atspi_driver._accessible_id(node), expected)
        if key == "application":
            return _matches_value(atspi_driver._application_name(node), expected)
        if key == "window":
            return _matches_value(atspi_driver._window_name(node), expected)
        if key == "parent":
            if not isinstance(expected, Mapping):
                return False
            parent = atspi_driver._parent(node)
            return parent is not None and atspi_driver._matches_criteria(parent, expected)
        if key == "hierarchy":
            if not isinstance(expected, (list, tuple)):
                return False
            actual = atspi_driver._hierarchy(node)
            wanted = tuple(str(item) for item in expected)
            return len(wanted) <= len(actual) and actual[-len(wanted):] == wanted if wanted else False
        if key.startswith("attribute:"):
            return _matches_value(atspi_driver._attributes(node).get(key.split(":", 1)[1]), expected)
        raise ValueError(f"unsupported AT-SPI locator property {key!r}")

    def jab_matches(info, title: str, identity: Mapping[str, Any]) -> bool:
        for key, expected in identity.items():
            if key == "name" and not _matches_value(info.name, expected):
                return False
            if key == "role" and not _matches_value(info.role_en_US, expected, case_insensitive=True):
                return False
            if key in {"application", "window"} and not _matches_value(title, expected):
                return False
            if key == "accessible_id":
                return False
            if key in {"parent", "hierarchy"}:
                return False
        return True

    component_repository._validate_locator_conditions = validate_locator_conditions
    atspi_driver._matches_condition = atspi_matches_condition
    atspi_driver._matches_value = _matches_value
    java_accessibility.JavaAccessBridgeDriver._matches = staticmethod(jab_matches)
