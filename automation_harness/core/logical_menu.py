"""Logical menu ownership and subobject matching.

JavaFX renders Menu/MenuItem values through disposable skin nodes.  Repository
identity must therefore attach menu descendants to a durable owner and retain
only selectors for the logical MenuItem graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

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
    bridge endpoint metadata.  The live ERSA recordings demonstrate that those
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


def find_logical_menu_targets(
    definitions: Iterable[ComponentDefinition], capture: CapturedComponent
) -> tuple[LogicalMenuTarget, ...]:
    """Find repository menu subobjects matching a captured logical descendant."""
    criteria = durable_menu_criteria(capture)
    if not criteria:
        return ()
    matches: list[LogicalMenuTarget] = []
    for definition in definitions:
        if definition.framework not in (None, "javafx"):
            continue
        _walk_subobjects(definition.component_id, definition.subobjects, (), (), criteria, matches)
    return tuple(matches)


def _walk_subobjects(
    owner_component_id: str,
    subobjects: Mapping[str, Mapping[str, Any]],
    path: tuple[str, ...],
    selectors: tuple[Mapping[str, Any], ...],
    captured: Mapping[str, Any],
    matches: list[LogicalMenuTarget],
) -> None:
    for subobject_id, raw in subobjects.items():
        selector = raw.get("selector", {}) if isinstance(raw, Mapping) else {}
        selector = selector if isinstance(selector, Mapping) else {}
        expected = selector.get("criteria", {})
        expected = expected if isinstance(expected, Mapping) else {}
        next_path = path + (str(subobject_id),)
        next_selectors = selectors + (dict(selector),)
        if expected and _criteria_match(expected, captured):
            matches.append(LogicalMenuTarget(owner_component_id, next_path, next_selectors))
        nested = raw.get("subobjects", {}) if isinstance(raw, Mapping) else {}
        if isinstance(nested, Mapping):
            _walk_subobjects(owner_component_id, nested, next_path, next_selectors, captured, matches)


def _criteria_match(expected: Mapping[str, Any], captured: Mapping[str, Any]) -> bool:
    # IDs are authoritative when both sides have one.  Text supplements ID and
    # becomes the primary selector only when no ID exists.
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
        "subobject_path": list(target.subobject_path),
    }
