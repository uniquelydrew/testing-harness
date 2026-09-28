"""Menu inventory lifecycle metadata."""
from __future__ import annotations

from typing import Any, Mapping

from automation_harness.models.gui import ObjectType


MENU_OWNER_TYPES = frozenset({
    ObjectType.MENU_BAR,
    ObjectType.MENU,
    ObjectType.CONTEXT_MENU,
})


def inventory_metadata(
    object_type,
    subobjects: Mapping[str, Mapping[str, Any]],
    *,
    complete: bool,
    source: str,
):
    try:
        semantic_type = (
            object_type if isinstance(object_type, ObjectType)
            else ObjectType(str(object_type))
        )
    except ValueError:
        return {}
    if semantic_type not in MENU_OWNER_TYPES or not subobjects:
        return {}
    return {
        "menu_inventory_status": "complete" if complete else "partial",
        "menu_inventory_dynamic": not complete,
        "menu_inventory_source": str(source),
        "menu_inventory_item_count": _count_items(subobjects),
    }


def with_inventory_metadata(
    properties: Mapping[str, Any],
    object_type,
    subobjects: Mapping[str, Mapping[str, Any]],
    *,
    complete: bool,
    source: str,
):
    result = dict(properties or {})
    result.update(inventory_metadata(
        object_type, subobjects, complete=complete, source=source,
    ))
    return result


def _count_items(subobjects):
    count = 0
    for raw in subobjects.values():
        if not isinstance(raw, Mapping):
            continue
        count += 1
        nested = raw.get("subobjects")
        if isinstance(nested, Mapping):
            count += _count_items(nested)
    return count
