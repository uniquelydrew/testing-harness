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
