from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


HIERARCHY_SCHEMA = "object-hierarchy/v2"

# Only non-runtime bookkeeping labels are discarded. Toolkit containers such as
# JPanel/JFXPanel/Pane are real runtime objects and must remain in lineage so
# every repository parent can be independently resolved and highlighted.
_NON_OBJECT_WRAPPERS = frozenset({
    "application", "desktop", "root", "scene", "window",
})

_MUTABLE_FIELDS = frozenset({
    "name", "text", "accessible_text", "description", "bounds", "geometry",
    "visible", "showing", "enabled", "disabled", "focused", "selected",
    "checked", "pressed", "expanded", "active", "sensitive", "state",
    "sibling_index", "sibling_count", "row_count", "column_count", "item_count",
})

_STABLE_FIELDS = frozenset({
    "accessible_id", "id", "role", "accessible_role", "application", "window",
    "parent", "parent_accessible_id", "parent_role", "parent_name",
    "framework", "native_class", "class", "user_data", "lineage",
})


@dataclass(frozen=True)
class HierarchySegment:
    label: str
    source: str = "capture"
    kind: str = "container"

    def to_dict(self) -> dict[str, str]:
        return {"label": self.label, "source": self.source, "kind": self.kind}


def condense_labels(labels, *, window: str | None = None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return concrete runtime ancestry and discarded non-object labels.

    The old named-semantic condensation removed toolkit containers and could
    consequently create logical repository parents that had no independently
    resolvable runtime object. Lineage now preserves concrete containers; only
    desktop/application/window/scene bookkeeping and duplicates are removed.
    """
    retained: list[str] = []
    discarded: list[str] = []
    window_key = str(window or "").strip().casefold()
    for raw in labels or ():
        label = str(raw or "").strip()
        normalized = " ".join(label.casefold().replace("_", " ").split())
        if not label or normalized == window_key:
            if label:
                discarded.append(label)
            continue
        if normalized in _NON_OBJECT_WRAPPERS:
            discarded.append(label)
            continue
        if retained and retained[-1].casefold() == label.casefold():
            discarded.append(label)
            continue
        retained.append(label)
    return tuple(retained), tuple(discarded)


def hierarchy_contract(captured) -> dict[str, Any]:
    """Build persisted ownership using only concrete runtime ancestry."""
    window = getattr(captured, "window", None)
    application = getattr(captured, "application", None)
    retained, discarded = condense_labels(getattr(captured, "hierarchy", ()), window=window)
    parent = {}
    for key in ("accessible_id", "name", "role"):
        value = getattr(captured, "parent_" + key, None)
        if value not in (None, ""):
            parent[key] = value

    target = {}
    for key in ("accessible_id", "role", "name", "framework", "native_class"):
        value = getattr(captured, key, None)
        if value not in (None, ""):
            target[key] = value

    ownership = {
        key: value
        for key, value in (
            ("application", application),
            ("window", window),
            ("framework", getattr(captured, "framework", None)),
        )
        if value not in (None, "")
    }
    return {
        "schema": HIERARCHY_SCHEMA,
        "ownership": ownership,
        "path": [HierarchySegment(label).to_dict() for label in retained],
        "target": target,
        "condensation": {
            "algorithm": "concrete-runtime-ancestry",
            "removed_non_objects": list(discarded),
            "removed_count": len(discarded),
        },
        **({"parent": parent} if parent else {}),
    }


def flatten_identity(identity: Mapping[str, Any] | None, prefix: str = "") -> dict[str, Any]:
    """Flatten nested identity evidence into comparable dotted keys."""
    result: dict[str, Any] = {}
    if not isinstance(identity, Mapping):
        return result
    for key, value in identity.items():
        path = "%s.%s" % (prefix, key) if prefix else str(key)
        if isinstance(value, Mapping):
            result.update(flatten_identity(value, path))
        else:
            result[path] = value
    return result


def classify_property(path: str) -> str:
    """Classify recapture evidence as immutable, mutable, or review."""
    leaf = str(path).rsplit(".", 1)[-1].casefold()
    normalized = str(path).casefold()
    if leaf in _MUTABLE_FIELDS or any(token in normalized for token in ("bounds", "geometry", "state.")):
        return "mutable"
    if leaf in _STABLE_FIELDS or any(
        token in normalized for token in ("accessible_id", "parent.", "ancestor", "lineage")
    ):
        return "stable"
    return "review"


def compare_identity_evidence(previous: Mapping[str, Any] | None, current: Mapping[str, Any] | None) -> dict[str, Any]:
    """Compare old and recaptured identity evidence without mutating either."""
    old = flatten_identity(previous)
    new = flatten_identity(current)
    changed = {}
    for key in sorted(set(old) | set(new)):
        if old.get(key) != new.get(key):
            changed[key] = {
                "before": old.get(key),
                "after": new.get(key),
                "classification": classify_property(key),
            }
    return {
        "changed": changed,
        "stable_changes": {key: value for key, value in changed.items() if value["classification"] == "stable"},
        "mutable_changes": {key: value for key, value in changed.items() if value["classification"] == "mutable"},
        "review_changes": {key: value for key, value in changed.items() if value["classification"] == "review"},
    }


def identity_from_definition(definition) -> Mapping[str, Any] | None:
    for strategy in getattr(definition, "strategies", ()) or ():
        value = getattr(strategy, "options", {}).get("identification")
        if isinstance(value, Mapping):
            return value
    return None


def recapture_comparison(existing, captured, proposed_definition) -> dict[str, Any]:
    """Return a reviewable recapture comparison preserving logical identity."""
    previous = identity_from_definition(existing)
    current = identity_from_definition(proposed_definition)
    comparison = compare_identity_evidence(previous, current)
    comparison.update({
        "component_id": existing.component_id,
        "object_id": existing.object_id,
        "previous_revision": existing.revision,
        "proposed_revision": proposed_definition.revision,
        "hierarchy_before": dict(existing.scope or {}).get("ownership"),
        "hierarchy_after": dict(proposed_definition.scope or {}).get("ownership"),
        "capture": hierarchy_contract(captured),
    })
    return comparison
