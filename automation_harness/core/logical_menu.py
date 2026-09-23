"""Logical menu ownership and subobject matching.

JavaFX renders Menu/MenuItem values through disposable skin nodes. Repository
identity must therefore attach menu descendants to a durable owner and retain
only selectors for the logical MenuItem graph.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, MutableMapping

from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType

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
    """Validate and copy the canonical menu-subobject schema."""
    result: dict[str, dict[str, Any]] = {}
    for key, raw in subobjects.items():
        if not isinstance(raw, Mapping):
            raise ValueError("menu subobject %r must be a mapping" % key)
        item = dict(raw)
        if "selector" in item:
            raise ValueError(
                "menu subobject %r uses removed selector wrapper; store kind/criteria directly" % key
            )
        criteria = item.get("criteria")
        if not isinstance(criteria, Mapping) or not criteria:
            raise ValueError("menu subobject %r requires non-empty criteria" % key)
        item["criteria"] = dict(criteria)
        nested = item.get("subobjects")
        if isinstance(nested, Mapping):
            item["subobjects"] = normalize_menu_subobjects(nested)
        result[str(key)] = item
    return result


def logical_menu_route(capture: CapturedComponent | None) -> tuple[Mapping[str, Any], ...]:
    if capture is None or capture.framework != "javafx":
        return ()
    metadata = capture.backend_properties.get("logical_menu")
    if not isinstance(metadata, Mapping):
        return ()
    raw_path = metadata.get("path")
    if not isinstance(raw_path, (list, tuple)):
        return ()
    return tuple(
        selector for selector in (
            _canonical_selector(item) for item in raw_path if isinstance(item, Mapping)
        )
        if selector.get("criteria")
    )


def is_javafx_menu_capture(capture: CapturedComponent | None) -> bool:
    if capture is None or capture.framework != "javafx":
        return False
    role = (capture.role or "").casefold().replace("_", " ")
    native = str(capture.native_class or "").rsplit(".", 1)[-1].casefold()
    return bool(logical_menu_route(capture)) or role in _MENU_ROLES or native in {
        "menubar", "menubutton", "splitmenubutton", "menubarbutton",
    } or capture.semantic_type() in {
        ObjectType.MENU_BAR, ObjectType.MENU, ObjectType.MENU_ITEM, ObjectType.CONTEXT_MENU,
    }


def is_terminal_menu_capture(capture: CapturedComponent | None) -> bool:
    route = logical_menu_route(capture)
    if route:
        kind = str(route[-1].get("kind") or "").casefold().replace("_", " ")
        return kind not in {"menu", "menu bar", "menu button"}
    return capture is not None and capture.semantic_type() == ObjectType.MENU_ITEM


def ensure_recorded_menu_owner(repository, capture: CapturedComponent) -> ComponentDefinition | None:
    """Materialize a logical ContextMenu owner when recording discovers one.

    MenuBar/Menu owners should already exist as independently captured controls
    and are matched by id/text. A ContextMenu is different: it may exist only
    while the popup is open. If the recording agent identifies a ContextMenu
    owner and the repository has none, add one logical owner so all subsequently
    recorded MenuItems nest under the same component rather than becoming
    top-level objects.
    """
    properties = dict(capture.backend_properties or {})
    metadata = properties.get("logical_menu")
    if not isinstance(metadata, Mapping):
        return None
    owner = metadata.get("owner")
    if not isinstance(owner, Mapping):
        return None
    kind = str(owner.get("kind") or "").replace("_", " ").casefold()
    if kind != "context menu":
        return None

    existing = [
        definition for definition in repository.components.values()
        if definition.framework in (None, "javafx")
        and getattr(definition.object_type, "value", "") == "context_menu"
        and _definition_matches_owner(definition, owner)
    ]
    if len(existing) == 1:
        return existing[0]
    if len(existing) > 1:
        return None

    popup = owner.get("popup")
    popup = popup if isinstance(popup, Mapping) else {}
    popup_id = popup.get("id")
    base = str(popup_id or "ContextMenu")
    component_id = base
    suffix = 2
    while component_id in repository.components:
        component_id = "%s-%d" % (base, suffix)
        suffix += 1

    mandatory = {"class": "javafx.scene.control.ContextMenu"}
    if popup_id not in (None, ""):
        mandatory = {"id": popup_id}
    definition = ComponentDefinition(
        component_id=component_id,
        description="Logical JavaFX context menu",
        strategies=(ComponentStrategy("javafx", {"identification": {"mandatory": mandatory}}),),
        actions=frozenset({"resolve", "select_menu_item"}),
        object_type=ObjectType.CONTEXT_MENU,
        framework="javafx",
        native_class="javafx.scene.control.ContextMenu",
        properties={"logical_owner": "context_menu"},
        subobjects={},
    )
    # ComponentRepository is an immutable value at the API level, but its map is
    # intentionally shared with the authoring session. Mutating this single new
    # entry lets the workbench/save flow see the discovered owner immediately.
    repository.components[component_id] = definition
    return definition


def find_logical_menu_targets(
    definitions: Iterable[ComponentDefinition], capture: CapturedComponent
) -> tuple[LogicalMenuTarget, ...]:
    criteria = durable_menu_criteria(capture)
    route = logical_menu_route(capture)
    metadata = capture.backend_properties.get("logical_menu")
    owner = metadata.get("owner") if isinstance(metadata, Mapping) else None
    if not criteria or not route or not isinstance(owner, Mapping):
        return ()
    definitions = tuple(definitions)
    matches: list[LogicalMenuTarget] = []
    for definition in definitions:
        if definition.framework not in (None, "javafx"):
            continue
        if not _definition_matches_owner(definition, owner):
            continue
        normalized = normalize_menu_subobjects(definition.subobjects)
        _replace_mutable_subobjects(definition.subobjects, normalized)
        _walk_subobjects(definition.component_id, normalized, (), (), criteria, matches,
                         expected_route=route)
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
    owner = metadata.get("owner")
    if not isinstance(owner, Mapping):
        return None
    selectors = logical_menu_route(capture)
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
            if isinstance(selector.get("relative_offset"), Mapping):
                raw["relative_offset"] = dict(selector["relative_offset"])
            current[key] = raw
        path.append(key)
        persisted_selectors.append({
            k: v for k, v in raw.items()
            if k in {"kind", "criteria", "ordinal", "relative_offset"}
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
    offset = value.get("relative_offset")
    if isinstance(offset, Mapping):
        normalized_offset = {}
        for key in ("x", "y", "tolerance"):
            number = offset.get(key)
            if isinstance(number, (int, float)) and not isinstance(number, bool):
                normalized_offset[key] = float(number)
        if "x" in normalized_offset and "y" in normalized_offset:
            normalized_offset.setdefault("tolerance", 16.0)
            result["relative_offset"] = normalized_offset
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
        return (not popup_id and getattr(definition.object_type, "value", "") == "context_menu"
                and definition.properties.get("logical_owner") == "context_menu")
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
            if candidate is not None:
                if candidate == expected_id:
                    return True
                continue
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
    expected_route: tuple[Mapping[str, Any], ...] = (),
) -> None:
    for subobject_id, raw in subobjects.items():
        if not isinstance(raw, Mapping):
            continue
        expected = raw.get("criteria", {})
        expected = expected if isinstance(expected, Mapping) else {}
        selector = {
            key: value for key, value in raw.items()
            if key in {"kind", "criteria", "ordinal", "relative_offset"}
        }
        next_path = path + (str(subobject_id),)
        next_selectors = selectors + (selector,)
        route_matches = len(next_selectors) == len(expected_route) and all(
            _selectors_equivalent(actual, wanted)
            for actual, wanted in zip(next_selectors, expected_route)
        )
        # Menu owners represented by their top-level Menu omit that first
        # selector from their persisted subobject path.
        owner_relative = expected_route[1:] if expected_route and len(next_selectors) == len(expected_route) - 1 else ()
        route_matches = route_matches or bool(owner_relative) and all(
            _selectors_equivalent(actual, wanted)
            for actual, wanted in zip(next_selectors, owner_relative)
        )
        if expected and _criteria_match(expected, captured) and route_matches:
            matches.append(LogicalMenuTarget(owner_component_id, next_path, next_selectors))
        nested = raw.get("subobjects", {})
        if isinstance(nested, Mapping):
            _walk_subobjects(owner_component_id, nested, next_path, next_selectors, captured, matches,
                             expected_route=expected_route)


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


def resolve_authored_menu_route(
    definition: ComponentDefinition,
    value: str | Iterable[str],
) -> tuple[str, ...]:
    """Resolve a readable route into the canonical owner-relative path."""
    if isinstance(value, str):
        segments = tuple(item.strip() for item in value.replace("/", ">").split(">") if item.strip())
    else:
        segments = tuple(str(item).strip() for item in value if str(item).strip())
    if not segments:
        raise ValueError("menu route must contain at least one segment")

    current = normalize_menu_subobjects(definition.subobjects)
    path: list[str] = []
    terminal: Mapping[str, Any] | None = None
    for segment in segments:
        matches = []
        wanted = segment.casefold()
        for key, selector in current.items():
            criteria = selector.get("criteria", {})
            aliases = {
                str(key).casefold(),
                str(criteria.get("id") or "").casefold(),
                str(criteria.get("text") or "").casefold(),
            }
            if wanted in aliases:
                matches.append((str(key), selector))
        if len(matches) != 1:
            raise ValueError(
                "menu route segment %r has %d matches under %r"
                % (segment, len(matches), " > ".join(path) or definition.component_id)
            )
        key, terminal = matches[0]
        path.append(key)
        nested = terminal.get("subobjects", {})
        current = nested if isinstance(nested, Mapping) else {}

    kind = str((terminal or {}).get("kind") or "").casefold().replace("_", " ")
    if kind in {"menu", "menu bar", "menu button"}:
        raise ValueError("menu route must terminate at an actionable menu item")
    return tuple(path)
