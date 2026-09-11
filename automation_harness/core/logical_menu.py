"""Logical menu ownership and subobject matching.

JavaFX renders Menu/MenuItem values through disposable skin nodes. Repository
identity must therefore attach menu descendants to a durable owner and retain
only selectors for the logical MenuItem graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, MutableMapping

from automation_harness.models.component import CapturedComponent, ComponentDefinition

_INTERNAL_MENU_SKINS = (
    "com.sun.javafx.scene.control.ContextMenuContent$MenuItemContainer",
    "com.sun.javafx.scene.control.MenuBarButton",
)
_MENU_ROLES = frozenset({"menu", "menu item", "menu_item", "check menu item", "radio menu item"})


@dataclass(frozen=True)
class LogicalMenuTarget:
    owner_component_id: str
    subobject_path: tuple[str, ...]
    selectors: tuple[Mapping[str, Any], ...]


def is_javafx_menu_skin_capture(capture: CapturedComponent | None) -> bool:
    if capture is None or capture.framework != "javafx":
        return False
    native = capture.native_class or ""
    role = (capture.role or "").casefold().replace("_", " ")
    return native.startswith(_INTERNAL_MENU_SKINS) or role in _MENU_ROLES


def durable_menu_criteria(capture: CapturedComponent) -> dict[str, Any]:
    """Extract selector evidence that belongs to the logical MenuItem.

    Never return window, CSS class, skin class, literal hierarchy, node ref, or
    bridge endpoint metadata. The live ERSA recordings demonstrate that those
    values describe ContextMenuContent/MenuItemContainer instances rather than
    application menu identity.
    """
    criteria: dict[str, Any] = {}
    if capture.accessible_id:
        criteria["id"] = capture.accessible_id
    name = capture.name
    if name and name != capture.accessible_id:
        criteria["text"] = name
    return criteria


def normalize_menu_subobjects(
    subobjects: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return the canonical runtime shape for nested menu selectors.

    Earlier capture code persisted ``selector: {criteria, ordinal}`` while the
    runtime executor consumes ``criteria`` and ``ordinal`` directly on each
    subobject. Accept both representations so existing repositories migrate in
    memory without forcing users to recapture menus.
    """
    result: dict[str, dict[str, Any]] = {}
    for key, raw in subobjects.items():
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        selector = item.pop("selector", None)
        if isinstance(selector, Mapping):
            if "criteria" not in item and isinstance(selector.get("criteria"), Mapping):
                item["criteria"] = dict(selector["criteria"])
            if "ordinal" not in item and selector.get("ordinal") is not None:
                item["ordinal"] = selector.get("ordinal")
        nested = item.get("subobjects")
        if isinstance(nested, Mapping):
            item["subobjects"] = normalize_menu_subobjects(nested)
        result[str(key)] = item
    return result


def find_logical_menu_targets(
    definitions: Iterable[ComponentDefinition], capture: CapturedComponent
) -> tuple[LogicalMenuTarget, ...]:
    """Find or attach a repository menu subobject for a captured descendant.

    Existing subobjects win. If the recording agent supplied a logical menu
    owner/path and that owner resolves uniquely to an existing repository
    component, a newly observed path is added beneath that owner in memory.
    This prevents a newly encountered MenuItem from becoming another top-level
    repository object.
    """
    criteria = durable_menu_criteria(capture)
    if not criteria:
        return ()
    definitions = tuple(definitions)
    matches: list[LogicalMenuTarget] = []
    for definition in definitions:
        if definition.framework not in (None, "javafx"):
            continue
        normalized = normalize_menu_subobjects(definition.subobjects)
        _replace_mutable_subobjects(definition.subobjects, normalized)
        _walk_subobjects(definition.component_id, normalized, (), (), criteria, matches)
    if matches:
        return tuple(matches)
    attached = _attach_recorded_menu_path(definitions, capture)
    return (attached,) if attached is not None else ()


def _attach_recorded_menu_path(
    definitions: tuple[ComponentDefinition, ...], capture: CapturedComponent
) -> LogicalMenuTarget | None:
    properties = dict(capture.backend_properties or {})
    metadata = properties.get("logical_menu")
    if not isinstance(metadata, Mapping):
        return None
    raw_path = metadata.get("path")
    owner = metadata.get("owner")
    if not isinstance(raw_path, (list, tuple)) or not isinstance(owner, Mapping):
        return None

    selectors = tuple(_canonical_selector(item) for item in raw_path if isinstance(item, Mapping))
    selectors = tuple(item for item in selectors if item.get("criteria"))
    if not selectors:
        return None

    owner_candidates = [
        definition for definition in definitions
        if definition.framework in (None, "javafx") and _definition_matches_owner(definition, owner)
    ]
    if len(owner_candidates) != 1:
        return None
    definition = owner_candidates[0]

    owner_selector = _owner_selector(owner)
    if owner_selector is not None and selectors and _selectors_equivalent(selectors[0], owner_selector):
        selectors = selectors[1:]
    if not selectors:
        return None

    if not isinstance(definition.subobjects, MutableMapping):
        return None
    current = definition.subobjects
    path: list[str] = []
    persisted_selectors: list[Mapping[str, Any]] = []
    for index, selector in enumerate(selectors):
        key = _subobject_key(selector, index)
        existing_key = _matching_child_key(current, selector)
        if existing_key is not None:
            key = existing_key
            raw = current[key]
        else:
            raw = {
                "kind": str(selector.get("kind") or "menu_item").replace(" ", "_"),
                "criteria": dict(selector.get("criteria") or {}),
            }
            if selector.get("ordinal") is not None:
                raw["ordinal"] = selector["ordinal"]
            current[key] = raw
        path.append(key)
        persisted_selectors.append({
            k: v for k, v in raw.items() if k in {"kind", "criteria", "ordinal"}
        })
        if index < len(selectors) - 1:
            nested = raw.get("subobjects")
            if not isinstance(nested, MutableMapping):
                nested = {}
                raw["subobjects"] = nested
            current = nested

    return LogicalMenuTarget(definition.component_id, tuple(path), tuple(persisted_selectors))


def _canonical_selector(value: Mapping[str, Any]) -> dict[str, Any]:
    criteria = value.get("criteria")
    criteria = dict(criteria) if isinstance(criteria, Mapping) else {}
    result = {
        "kind": str(value.get("kind") or "menu_item").replace(" ", "_"),
        "criteria": {
            key: item for key, item in criteria.items()
            if key in {"id", "text"} and item not in (None, "")
        },
    }
    if isinstance(value.get("ordinal"), int) and not isinstance(value.get("ordinal"), bool):
        result["ordinal"] = value["ordinal"]
    return result


def _definition_matches_owner(definition: ComponentDefinition, owner: Mapping[str, Any]) -> bool:
    selector = _owner_selector(owner)
    if selector is not None:
        criteria = selector.get("criteria", {})
        if isinstance(criteria, Mapping) and _definition_matches_criteria(definition, criteria):
            return True

    kind = str(owner.get("kind") or "").replace("_", " ").casefold()
    if kind == "context menu":
        popup = owner.get("popup")
        popup = popup if isinstance(popup, Mapping) else {}
        popup_id = popup.get("id")
        if popup_id and _definition_matches_criteria(definition, {"id": popup_id}):
            return True
        return getattr(definition.object_type, "value", "") == "context_menu"
    return False


def _owner_selector(owner: Mapping[str, Any]) -> Mapping[str, Any] | None:
    logical = owner.get("logical")
    if isinstance(logical, Mapping):
        return _canonical_selector(logical)
    node = owner.get("node")
    if isinstance(node, Mapping):
        criteria = {}
        node_id = node.get("accessible_id") or node.get("id")
        name = node.get("name") or node.get("text")
        if node_id not in (None, ""):
            criteria["id"] = node_id
        if name not in (None, ""):
            criteria["text"] = name
        if criteria:
            return {"kind": str(owner.get("kind") or "menu").replace(" ", "_"), "criteria": criteria}
    return None


def _definition_matches_criteria(definition: ComponentDefinition, criteria: Mapping[str, Any]) -> bool:
    expected_id = criteria.get("id")
    expected_text = criteria.get("text")
    for strategy in definition.strategies:
        if strategy.type != "javafx" or not isinstance(strategy.options, Mapping):
            continue
        identification = strategy.options.get("identification")
        if not isinstance(identification, Mapping):
            continue
        mandatory = identification.get("mandatory", identification)
        assistive = identification.get("assistive", {})
        if not isinstance(mandatory, Mapping) or not isinstance(assistive, Mapping):
            continue
        if expected_id not in (None, ""):
            candidate = mandatory.get("id") or assistive.get("id")
            if candidate == expected_id:
                return True
        if expected_text not in (None, ""):
            candidate = mandatory.get("text") or assistive.get("text")
            if candidate == expected_text:
                return True
    return False


def _matching_child_key(
    subobjects: Mapping[str, Mapping[str, Any]], selector: Mapping[str, Any]
) -> str | None:
    expected = selector.get("criteria")
    if not isinstance(expected, Mapping):
        return None
    for key, raw in subobjects.items():
        if not isinstance(raw, Mapping):
            continue
        actual = raw.get("criteria")
        if isinstance(actual, Mapping) and _criteria_match(actual, expected):
            return str(key)
    return None


def _selectors_equivalent(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_criteria = left.get("criteria")
    right_criteria = right.get("criteria")
    return (
        isinstance(left_criteria, Mapping)
        and isinstance(right_criteria, Mapping)
        and _criteria_match(left_criteria, right_criteria)
    )


def _subobject_key(selector: Mapping[str, Any], index: int) -> str:
    criteria = selector.get("criteria")
    criteria = criteria if isinstance(criteria, Mapping) else {}
    raw = str(criteria.get("id") or criteria.get("text") or "menu_%d" % (index + 1))
    value = "".join(character.lower() if character.isalnum() else "_" for character in raw).strip("_")
    return value or "menu_%d" % (index + 1)


def _replace_mutable_subobjects(
    original: Mapping[str, Mapping[str, Any]], normalized: Mapping[str, Mapping[str, Any]]
) -> None:
    if not isinstance(original, MutableMapping):
        return
    original.clear()
    original.update({key: dict(value) for key, value in normalized.items()})


def _walk_subobjects(
    owner_component_id: str,
    subobjects: Mapping[str, Mapping[str, Any]],
    path: tuple[str, ...],
    selectors: tuple[Mapping[str, Any], ...],
    captured: Mapping[str, Any],
    matches: list[LogicalMenuTarget],
) -> None:
    for subobject_id, raw in subobjects.items():
        if not isinstance(raw, Mapping):
            continue
        expected = raw.get("criteria", {})
        expected = expected if isinstance(expected, Mapping) else {}
        selector = {
            key: value for key, value in raw.items()
            if key in {"kind", "criteria", "ordinal"}
        }
        next_path = path + (str(subobject_id),)
        next_selectors = selectors + (selector,)
        if expected and _criteria_match(expected, captured):
            matches.append(LogicalMenuTarget(owner_component_id, next_path, next_selectors))
        nested = raw.get("subobjects", {})
        if isinstance(nested, Mapping):
            _walk_subobjects(owner_component_id, nested, next_path, next_selectors, captured, matches)


def _criteria_match(expected: Mapping[str, Any], captured: Mapping[str, Any]) -> bool:
    expected_id = expected.get("id")
    captured_id = captured.get("id")
    if expected_id not in (None, "") and captured_id not in (None, ""):
        return str(expected_id) == str(captured_id)
    expected_text = expected.get("text")
    captured_text = captured.get("text")
    if expected_text not in (None, "") and captured_text not in (None, ""):
        return str(expected_text) == str(captured_text)
    return False


def menu_action_payload(target: LogicalMenuTarget) -> dict[str, Any]:
    return {
        "type": "select_menu_item",
        "path": list(target.subobject_path),
    }
