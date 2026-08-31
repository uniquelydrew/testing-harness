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


def _contains_regex(value: Any) -> bool:
    if isinstance(value, Mapping):
        if set(value) == {"regex"}:
            return True
        return any(_contains_regex(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_regex(item) for item in value)
    return False


def _exact_subset(value: Any) -> Any:
    """Return the safely bridge-evaluable subset of a matcher structure."""
    if isinstance(value, Mapping):
        if set(value) == {"regex"}:
            return None
        result = {}
        for key, item in value.items():
            exact = _exact_subset(item)
            if exact is not None:
                result[key] = exact
        return result or None
    if isinstance(value, (list, tuple)):
        if _contains_regex(value):
            return None
        return list(value)
    return value


def _structure_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping) and set(expected) == {"regex"}:
        return _matches_value(actual, expected)
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            return False
        return all(
            key in actual and _structure_matches(actual.get(key), value)
            for key, value in expected.items()
        )
    if isinstance(expected, (list, tuple)):
        if not isinstance(actual, (list, tuple)) or len(actual) != len(expected):
            return False
        return all(_structure_matches(left, right) for left, right in zip(actual, expected))
    return actual == expected


def _javafx_node_matches(node: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    for key, expected in criteria.items():
        if key == "parent":
            if not isinstance(expected, Mapping) or not _structure_matches(node.get("parent"), expected):
                return False
            continue
        if key == "ancestor":
            if not isinstance(expected, Mapping):
                return False
            ancestors = node.get("stable_ancestors")
            if not isinstance(ancestors, (list, tuple)) or not any(
                isinstance(item, Mapping) and _structure_matches(item, expected)
                for item in ancestors
            ):
                return False
            continue
        if key == "lineage":
            if not isinstance(expected, (list, tuple)):
                return False
            ancestors = node.get("stable_ancestors")
            if not isinstance(ancestors, (list, tuple)):
                return False
            cursor = 0
            for candidate in ancestors:
                if cursor >= len(expected):
                    break
                wanted = expected[cursor]
                if isinstance(candidate, Mapping) and isinstance(wanted, Mapping) and _structure_matches(candidate, wanted):
                    cursor += 1
            if cursor != len(expected):
                return False
            continue
        if not _structure_matches(node.get(key), expected):
            return False
    return True


def install() -> None:
    """Install regex-aware locator behavior into repository and all GUI drivers."""
    from automation_harness.core import component_repository
    from automation_harness.drivers import atspi_driver, java_accessibility, javafx_bridge

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

    original_javafx_find_matches = javafx_bridge.JavaFxBridgeDriver._find_matches
    original_javafx_activate = javafx_bridge.JavaFxBridgeDriver.activate
    original_javafx_get_text = javafx_bridge.JavaFxBridgeDriver.get_text
    original_javafx_set_text = javafx_bridge.JavaFxBridgeDriver.set_text

    def javafx_find_matches(self, identification=None):
        identity = dict(identification or {})
        if not _contains_regex(identity):
            return original_javafx_find_matches(self, identification)

        if "mandatory" in identity:
            mandatory = identity.get("mandatory") or {}
            assistive = identity.get("assistive") or {}
        else:
            mandatory = {
                key: value
                for key, value in identity.items()
                if key not in {"assistive", "ordinal"}
            }
            assistive = identity.get("assistive") or {}
        if not isinstance(mandatory, Mapping) or not isinstance(assistive, Mapping):
            return original_javafx_find_matches(self, identification)

        broad_mandatory = _exact_subset(mandatory)
        broad_identity = {"mandatory": broad_mandatory or {}}
        candidates, _bridge_trace = original_javafx_find_matches(self, broad_identity)

        matches = [
            (endpoint, node)
            for endpoint, node in candidates
            if _javafx_node_matches(node, mandatory)
        ]
        stages = [
            javafx_bridge.JavaFxResolutionStage("mandatory", dict(mandatory), len(matches))
        ]
        for key, value in assistive.items():
            if len(matches) <= 1:
                break
            criterion = {key: value}
            matches = [
                (endpoint, node)
                for endpoint, node in matches
                if _javafx_node_matches(node, criterion)
            ]
            stages.append(
                javafx_bridge.JavaFxResolutionStage(
                    "assistive:" + str(key), criterion, len(matches)
                )
            )
        return matches, tuple(stages)

    def _javafx_ref_identity(node):
        ref = node.get("ref") if isinstance(node, Mapping) else None
        if not ref:
            raise LookupError("JavaFX bridge result has no stable runtime reference")
        return {"mandatory": {"ref": ref}}

    def javafx_activate(self, *, identification=None, **kwargs):
        if not _contains_regex(identification or {}):
            return original_javafx_activate(self, identification=identification, **kwargs)
        endpoint, node, _trace = self._find_unique(identification)
        response = endpoint.request(
            "activate", timeout=5.0, identification=_javafx_ref_identity(node)
        )
        return {
            "action": "activate",
            "bridge_pid": endpoint.pid,
            "node": response.get("node"),
        }

    def javafx_get_text(self, *, identification=None, **kwargs):
        if not _contains_regex(identification or {}):
            return original_javafx_get_text(self, identification=identification, **kwargs)
        endpoint, node, _trace = self._find_unique(identification)
        response = endpoint.request(
            "get_text", timeout=5.0, identification=_javafx_ref_identity(node)
        )
        return str(response.get("text") or "")

    def javafx_set_text(self, value, *, identification=None, **kwargs):
        if not _contains_regex(identification or {}):
            return original_javafx_set_text(self, value, identification=identification, **kwargs)
        endpoint, node, _trace = self._find_unique(identification)
        response = endpoint.request(
            "set_text",
            timeout=5.0,
            identification=_javafx_ref_identity(node),
            value=value,
        )
        return {
            "action": "set_text",
            "bridge_pid": endpoint.pid,
            "node": response.get("node"),
        }

    component_repository._validate_locator_conditions = validate_locator_conditions
    atspi_driver._matches_condition = atspi_matches_condition
    atspi_driver._matches_value = _matches_value
    java_accessibility.JavaAccessBridgeDriver._matches = staticmethod(jab_matches)
    javafx_bridge.JavaFxBridgeDriver._find_matches = javafx_find_matches
    javafx_bridge.JavaFxBridgeDriver.activate = javafx_activate
    javafx_bridge.JavaFxBridgeDriver.get_text = javafx_get_text
    javafx_bridge.JavaFxBridgeDriver.set_text = javafx_set_text
