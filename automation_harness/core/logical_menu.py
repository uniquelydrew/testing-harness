"""Logical menu ownership and subobject matching.

JavaFX renders Menu/MenuItem values through disposable skin nodes. Repository
identity must therefore attach menu descendants to a durable owner and retain
only selectors for the logical MenuItem graph.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

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
            if "relative_offset" not in item and isinstance(selector.get("relative_offset"), Mapping):
                item["relative_offset"] = dict(selector["relative_offset"])
        nested = item.get("subobjects")
        if isinstance(nested, Mapping):
            item["subobjects"] = normalize_menu_subobjects(nested)
        result[str(key)] = item
    return result


def logical_menu_metadata(capture: CapturedComponent | None) -> Mapping[str, Any] | None:
    if capture is None:
        return None
    properties = dict(capture.backend_properties or {})
    value = properties.get("logical_popup")
    if not isinstance(value, Mapping): value = properties.get("logical_menu")
    return value if isinstance(value, Mapping) else None


def ensure_recorded_menu_owner(repository, capture: CapturedComponent) -> ComponentDefinition | None:
    """Return the existing or proposed logical Context Menu owner without mutation.

    Persistence is deliberately handled by stage_recorded_menu_capture at
    authoring review time. Merely observing a transient menu must never modify
    the repository.
    """
    metadata = logical_menu_metadata(capture)
    if metadata is None:
        return None
    owner = metadata.get("owner")
    if not isinstance(owner, Mapping):
        return None
    candidates = _matching_owner_candidates(tuple(repository.components.values()), owner)
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        return None
    if str(owner.get("kind") or "").replace("_", " ").casefold() != "context menu":
        return None
    return _new_context_menu_owner(repository, owner)


def find_logical_menu_targets(
    definitions: Iterable[ComponentDefinition], capture: CapturedComponent
) -> tuple[LogicalMenuTarget, ...]:
    """Match menu descendants without mutating any repository definition."""
    criteria = durable_menu_criteria(capture)
    if not criteria:
        return ()
    definitions = tuple(definitions)
    matches: list[LogicalMenuTarget] = []
    for definition in definitions:
        normalized = normalize_menu_subobjects(definition.subobjects)
        _walk_subobjects(definition.component_id, normalized, (), (), criteria, matches)
    if matches:
        return tuple(matches)
    proposed = _preview_recorded_menu_path(definitions, capture)
    return (proposed,) if proposed is not None else ()


def stage_recorded_menu_capture(
    repository,
    capture: CapturedComponent,
):
    """Apply one accepted menu observation as an immutable repository update.

    Returns (repository, target, owner_created, inventory_changed). A target may
    already be fully persisted, in which case the original repository is
    returned unchanged.
    """
    metadata = logical_menu_metadata(capture)
    if metadata is None:
        return repository, None, False, False
    owner_metadata = metadata.get("owner")
    if not isinstance(owner_metadata, Mapping):
        return repository, None, False, False

    candidates = _matching_owner_candidates(
        tuple(repository.components.values()), owner_metadata,
    )
    owner_created = False
    if len(candidates) == 1:
        owner = candidates[0]
    elif len(candidates) > 1:
        return repository, None, False, False
    else:
        kind = str(owner_metadata.get("kind") or "").replace("_", " ").casefold()
        if kind != "context menu":
            return repository, None, False, False
        owner = _new_context_menu_owner(repository, owner_metadata)
        repository = repository.with_component(owner)
        owner_created = True

    normalized = normalize_menu_subobjects(owner.subobjects)
    target, updated_subobjects = _build_recorded_menu_path(
        owner, capture, normalized,
    )
    if target is None:
        return repository, None, owner_created, owner_created

    changed = updated_subobjects != normalized
    if changed:
        properties = dict(owner.properties or {})
        properties.update({
            "menu_inventory_status": "partial",
            "menu_inventory_dynamic": True,
        })
        owner = replace(
            owner,
            subobjects=updated_subobjects,
            properties=properties,
            revision=owner.revision + 1,
        )
        repository = repository.with_component(owner)
    return repository, target, owner_created, changed or owner_created


def _preview_recorded_menu_path(
    definitions: tuple[ComponentDefinition, ...],
    capture: CapturedComponent,
) -> LogicalMenuTarget | None:
    metadata = logical_menu_metadata(capture)
    if metadata is None:
        return None
    owner_metadata = metadata.get("owner")
    if not isinstance(owner_metadata, Mapping):
        return None
    candidates = _matching_owner_candidates(definitions, owner_metadata)
    if len(candidates) != 1:
        return None
    owner = candidates[0]
    target, _updated = _build_recorded_menu_path(
        owner, capture, normalize_menu_subobjects(owner.subobjects),
    )
    return target


def _build_recorded_menu_path(
    definition: ComponentDefinition,
    capture: CapturedComponent,
    source_subobjects: Mapping[str, Mapping[str, Any]],
):
    metadata = logical_menu_metadata(capture)
    if metadata is None:
        return None, dict(source_subobjects)
    raw_path = metadata.get("path")
    owner_metadata = metadata.get("owner")
    if not isinstance(raw_path, (list, tuple)) or not isinstance(owner_metadata, Mapping):
        return None, dict(source_subobjects)

    selectors = tuple(
        _canonical_selector(item) for item in raw_path if isinstance(item, Mapping)
    )
    selectors = tuple(
        item for item in selectors
        if item.get("criteria") or item.get("ordinal") is not None or item.get("relative_offset")
    )
    owner_selector = _owner_selector(owner_metadata)
    if owner_selector is not None and selectors and _selectors_equivalent(
        selectors[0], owner_selector,
    ):
        selectors = selectors[1:]
    if not selectors:
        return None, dict(source_subobjects)

    root = _copy_subobjects(source_subobjects)
    current = root
    path: list[str] = []
    persisted_selectors: list[Mapping[str, Any]] = []
    for index, selector in enumerate(selectors):
        key = _matching_child_key(current, selector) or _subobject_key(selector, index)
        existing = current.get(key)
        raw = dict(existing) if isinstance(existing, Mapping) else {}
        raw.update({
            "kind": str(selector.get("kind") or "menu_item").replace(" ", "_"),
            "criteria": dict(selector.get("criteria") or {}),
        })
        label = raw["criteria"].get("text") or raw["criteria"].get("id")
        if label not in (None, ""):
            raw.setdefault("display_name", str(label))
        raw.setdefault(
            "selectable",
            str(raw["kind"]).replace("_", " ").casefold() != "separator",
        )
        if selector.get("ordinal") is not None:
            raw["ordinal"] = selector["ordinal"]
        if isinstance(selector.get("relative_offset"), Mapping):
            raw["relative_offset"] = dict(selector["relative_offset"])
        current[key] = raw
        path.append(key)
        persisted_selectors.append({
            field: raw[field]
            for field in ("kind", "criteria", "ordinal", "relative_offset")
            if field in raw
        })
        if index < len(selectors) - 1:
            nested = raw.get("subobjects")
            nested_copy = _copy_subobjects(nested) if isinstance(nested, Mapping) else {}
            raw["subobjects"] = nested_copy
            current[key] = raw
            current = nested_copy

    return (
        LogicalMenuTarget(
            definition.component_id,
            tuple(path),
            tuple(persisted_selectors),
        ),
        root,
    )


def _matching_owner_candidates(
    definitions: tuple[ComponentDefinition, ...],
    owner: Mapping[str, Any],
) -> list[ComponentDefinition]:
    return [
        definition for definition in definitions
        if _definition_matches_owner(definition, owner)
    ]


def _new_context_menu_owner(repository, owner: Mapping[str, Any]) -> ComponentDefinition:
    popup = owner.get("popup")
    popup = popup if isinstance(popup, Mapping) else {}
    popup_id = popup.get("id")
    base = str(popup.get("name") or popup_id or "Context Menu").strip() or "Context Menu"
    component_id = base
    suffix = 2
    while component_id in repository.components:
        component_id = "%s %d" % (base, suffix)
        suffix += 1

    mandatory = {"class": "javafx.scene.control.ContextMenu"}
    if popup_id not in (None, ""):
        mandatory = {"id": popup_id}
    return ComponentDefinition(
        component_id=component_id,
        description="Logical context menu",
        strategies=(
            ComponentStrategy(
                "javafx",
                {"identification": {"mandatory": mandatory}},
            ),
        ),
        actions=frozenset({"resolve", "select_menu_item"}),
        object_type=ObjectType.CONTEXT_MENU,
        framework="javafx",
        native_class="javafx.scene.control.ContextMenu",
        properties={
            "logical_owner": "context_menu",
            "menu_inventory_status": "partial",
            "menu_inventory_dynamic": True,
        },
        subobjects={},
    )


def _copy_subobjects(value: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for key, raw in value.items():
        if not isinstance(raw, Mapping):
            continue
        item = dict(raw)
        nested = raw.get("subobjects")
        if isinstance(nested, Mapping):
            item["subobjects"] = _copy_subobjects(nested)
        result[str(key)] = item
    return result


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
    expected_id = criteria.get("id", criteria.get("accessible_id"))
    expected_text = criteria.get("text", criteria.get("name"))
    for strategy in definition.strategies:
        if not isinstance(strategy.options, Mapping):
            continue
        identification = strategy.options.get("identification")
        if not isinstance(identification, Mapping):
            continue
        mandatory = identification.get("mandatory", identification)
        assistive = identification.get("assistive", {})
        if not isinstance(mandatory, Mapping) or not isinstance(assistive, Mapping):
            continue

        def value(*keys):
            for key in keys:
                candidate = mandatory.get(key)
                if candidate not in (None, ""):
                    return candidate
                candidate = assistive.get(key)
                if candidate not in (None, ""):
                    return candidate
            return None

        if expected_id not in (None, ""):
            if str(value("id", "accessible_id") or "") == str(expected_id):
                return True
        if expected_text not in (None, ""):
            if str(value("text", "name", "accessible_text") or "") == str(expected_text):
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
            if key in {"kind", "criteria", "ordinal", "relative_offset"}
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


def attach_context_menu_invoker(
    repository,
    menu_component_id: str,
    invoking_component_id: str,
):
    """Attach a Context Menu to the concrete object that invoked it."""
    menu = repository.get(menu_component_id)
    invoker = repository.get(invoking_component_id)
    if menu.object_type != ObjectType.CONTEXT_MENU:
        return repository, False
    if menu.object_id == invoker.object_id:
        raise ValueError("context menu cannot be its own invoking object")
    if menu.owner_object_id == invoker.object_id:
        return repository, False
    properties = dict(menu.properties or {})
    properties.update({
        "invoking_object_id": invoker.object_id,
        "invoking_object_name": invoker.component_id,
    })
    updated = replace(
        menu,
        owner_object_id=invoker.object_id,
        properties=properties,
        revision=menu.revision + 1,
    )
    return repository.with_component(updated), True


def logical_menu_target_is_persisted(repository, target: LogicalMenuTarget) -> bool:
    if target.owner_component_id not in repository.components:
        return False
    current = normalize_menu_subobjects(
        repository.get(target.owner_component_id).subobjects
    )
    for segment in target.subobject_path:
        raw = current.get(segment)
        if not isinstance(raw, Mapping):
            return False
        nested = raw.get("subobjects")
        current = nested if isinstance(nested, Mapping) else {}
    return True


def menu_action_payload(target: LogicalMenuTarget) -> dict[str, Any]:
    return {
        "type": "select_menu_item",
        "path": list(target.subobject_path),
    }

find_logical_popup_targets = find_logical_menu_targets
stage_recorded_popup_capture = stage_recorded_menu_capture

def popup_action_payload(target: LogicalMenuTarget) -> dict[str, Any]:
    return {"type": "select_item", "path": list(target.subobject_path)}
