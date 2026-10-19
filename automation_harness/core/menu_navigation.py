"""Canonical user-facing menu navigation strings.

Plans expose readable navigation such as File > Export > PDF. Repository
subobject IDs remain the durable internal path and older list-valued plans are
accepted for backward compatibility.
"""
from __future__ import annotations

from typing import Any, Mapping


DELIMITER = " > "


def navigation_from_path(
    subobjects: Mapping[str, Mapping[str, Any]],
    path,
) -> str:
    current = subobjects
    labels = []
    for segment in path:
        raw = current.get(str(segment))
        if not isinstance(raw, Mapping):
            raise ValueError("menu path segment %r is not defined" % segment)
        labels.append(menu_item_label(str(segment), raw))
        nested = raw.get("subobjects")
        current = nested if isinstance(nested, Mapping) else {}
    if not labels:
        raise ValueError("menu path must not be empty")
    return DELIMITER.join(labels)


def resolve_navigation(
    subobjects: Mapping[str, Mapping[str, Any]],
    navigation,
):
    if isinstance(navigation, (list, tuple)):
        path = [str(item) for item in navigation if isinstance(item, str) and item]
        if not path or len(path) != len(navigation):
            raise ValueError("menu path must contain non-empty string segments")
        _validate_internal_path(subobjects, path)
        return path
    if not isinstance(navigation, str) or not navigation.strip():
        raise ValueError("menu navigation must be a non-empty string")

    labels = [item.strip() for item in navigation.split(">")]
    if not labels or any(not item for item in labels):
        raise ValueError(
            "menu navigation must use non-empty segments separated by ' > '"
        )

    current = subobjects
    path = []
    for label in labels:
        matches = []
        folded = label.casefold()
        for key, raw in current.items():
            if not isinstance(raw, Mapping):
                continue
            candidates = {
                str(key).casefold(),
                menu_item_label(str(key), raw).casefold(),
            }
            criteria = _criteria(raw)
            for field in ("text", "name", "accessible_name", "accessible_id", "id"):
                value = criteria.get(field)
                if value not in (None, ""):
                    candidates.add(str(value).casefold())
            if folded in candidates:
                matches.append((str(key), raw))
        if not matches:
            raise ValueError(
                "menu navigation segment %r is not defined under %r"
                % (label, DELIMITER.join(labels[:len(path)]) or "menu root")
            )
        if len(matches) > 1:
            raise ValueError(
                "menu navigation segment %r is ambiguous under %r"
                % (label, DELIMITER.join(labels[:len(path)]) or "menu root")
            )
        key, raw = matches[0]
        path.append(key)
        nested = raw.get("subobjects")
        current = nested if isinstance(nested, Mapping) else {}
    return path



def selectors_from_navigation(
    subobjects: Mapping[str, Mapping[str, Any]], navigation,
) -> list[dict[str, Any]]:
    """Build scoped menu selectors from captured inventory or explicit labels.

    Captured options provide preferred durable selectors, not an exhaustive
    allow-list: unobserved options use exact visible-text selectors. Internal
    ID paths, however, must resolve to their recorded definitions. A known
    disabled or non-selectable item is never converted into a fallback.
    """
    by_id = isinstance(navigation, (list, tuple))
    if by_id:
        segments = resolve_navigation(subobjects, navigation)
    else:
        if not isinstance(navigation, str) or not navigation.strip():
            raise ValueError("menu navigation must be a non-empty string")
        segments = [item.strip() for item in navigation.split(">")]
        if any(not item for item in segments):
            raise ValueError("menu navigation must use non-empty segments separated by ' > '")

    current = subobjects
    selectors: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        last = index == len(segments) - 1
        key = segment
        raw = None
        if by_id:
            raw = current.get(segment)
            if not isinstance(raw, Mapping):
                raise ValueError("menu path segment %r is not defined" % segment)
        else:
            matches = []
            for candidate_key, candidate in current.items():
                if not isinstance(candidate, Mapping):
                    continue
                names = {
                    str(candidate_key).casefold(),
                    menu_item_label(str(candidate_key), candidate).casefold(),
                }
                for field in ("text", "name", "accessible_name", "accessible_id", "id"):
                    value = _criteria(candidate).get(field)
                    if value not in (None, ""):
                        names.add(str(value).casefold())
                if segment.casefold() in names:
                    matches.append((str(candidate_key), candidate))
            if len(matches) > 1:
                raise ValueError("ambiguous menu option %r" % segment)
            if matches:
                key, raw = matches[0]

        if raw is None:
            selectors.append({
                "kind": "menu_item" if last else "menu",
                "criteria": {"text": segment},
            })
            current = {}
            continue

        if raw.get("enabled") is False:
            raise ValueError("menu option %r is disabled" % segment)
        if str(raw.get("kind") or "").replace(" ", "_").casefold() == "separator":
            raise ValueError("menu option %r is a separator" % segment)
        if last and raw.get("selectable") is False:
            raise ValueError("menu option %r is not selectable" % segment)

        stored = raw.get("selector")
        stored = stored if isinstance(stored, Mapping) else {}
        kind = raw.get("kind") or stored.get("kind") or ("menu_item" if last else "menu")
        criteria = _criteria(raw)
        selector = {"kind": str(kind).replace(" ", "_"), "criteria": dict(criteria) if criteria else {
            "text": menu_item_label(key, raw),
        }}
        for optional in ("ordinal", "relative_offset"):
            value = raw.get(optional, stored.get(optional))
            if value is not None:
                selector[optional] = value
        selectors.append(selector)
        nested = raw.get("subobjects")
        current = nested if isinstance(nested, Mapping) else {}

    return selectors


def menu_item_label(key: str, raw: Mapping[str, Any]) -> str:
    for field in ("display_name", "label", "text", "name"):
        value = raw.get(field)
        if value not in (None, ""):
            return str(value)
    criteria = _criteria(raw)
    for field in ("text", "name", "accessible_name", "accessible_id", "id"):
        value = criteria.get(field)
        if value not in (None, ""):
            return str(value)
    return key.replace("_", " ").strip().title() or "Menu Item"


def _criteria(raw):
    selector = raw.get("selector")
    if isinstance(selector, Mapping):
        criteria = selector.get("criteria")
        if isinstance(criteria, Mapping):
            return criteria
    criteria = raw.get("criteria")
    return criteria if isinstance(criteria, Mapping) else {}


def _validate_internal_path(subobjects, path):
    current = subobjects
    for segment in path:
        raw = current.get(segment)
        if not isinstance(raw, Mapping):
            raise ValueError("menu path segment %r is not defined" % segment)
        nested = raw.get("subobjects")
        current = nested if isinstance(nested, Mapping) else {}
