from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class PropertyPolicy:
    stability: str
    selected: bool
    selectable: bool
    reason: str


_SESSION_ONLY = {
    "ref",
    "node_ref",
    "bridge_pid",
    "bridge_port",
    "scene",
    "capture_source",
}

_RUNTIME_KEYS = {
    "visible",
    "disabled",
    "enabled",
    "focused",
    "managed",
    "focus_traversable",
    "selected",
    "showing",
    "editable",
    "expanded",
    "open",
    "item_count",
    "row_count",
    "column_count",
}

_LOW_KEYS = {
    "bounds",
    "sibling_index",
    "sibling_count",
    "hierarchy",
}


def property_policy(
    key: str,
    value: Any,
    *,
    candidate_section: str | None = None,
    source: str = "local",
    framework: str = "javafx",
) -> PropertyPolicy:
    """Return the default capture-time treatment for one matcher property.

    Capture collects broadly. This policy only controls whether evidence is
    initially selected as durable identity. The author may opt weak/runtime
    evidence in later unless it is process/session plumbing that cannot survive
    a restart.
    """
    normalized = str(key or "")
    leaf = normalized.rsplit(".", 1)[-1]

    if normalized in _SESSION_ONLY or leaf in _SESSION_ONLY:
        return PropertyPolicy("session", False, False, "session-only diagnostic")

    if source in {"inherited", "common"}:
        # Existing candidate conditions remain part of the effective identity
        # until the resolver is tree-relative. New redundant evidence is not
        # automatically selected.
        selected = candidate_section in {"mandatory", "assistive"}
        return PropertyPolicy(source, selected, False, source + " tree evidence")

    if framework != "javafx" and candidate_section is None:
        # The current AT-SPI repository validator accepts a narrower locator
        # vocabulary. Expose extra evidence, but do not let it produce an
        # invalid locator until that backend grows equivalent matcher support.
        return PropertyPolicy("diagnostic", False, False, "backend does not author this property")

    if normalized == "id":
        return PropertyPolicy("very high", True, True, "explicit application id")

    if normalized.startswith("properties."):
        prop = normalized[len("properties."):].casefold()
        if prop.startswith(("automation.", "test.", "qa.")):
            return PropertyPolicy("very high", True, True, "application-authored automation property")
        return PropertyPolicy("medium", False, True, "application property")

    if normalized in {"accessible_text", "text", "user_data"}:
        return PropertyPolicy("high", True, True, "stable semantic value")

    if normalized == "class" or normalized.endswith(".class"):
        text = str(value or "")
        if text.startswith(("com.sun.javafx.", "com.sun.glass.")):
            selected = candidate_section == "mandatory"
            return PropertyPolicy("low", selected, True, "JavaFX implementation class")
        return PropertyPolicy("high", True, True, "semantic class")

    if normalized == "window" or normalized.startswith("parent.") or normalized.startswith("ancestor") or normalized == "lineage":
        selected = candidate_section == "mandatory"
        return PropertyPolicy("high", selected, True, "structural scope")

    if normalized.startswith("layout."):
        selected = candidate_section == "mandatory"
        return PropertyPolicy("medium", selected, True, "layout discriminator")

    if normalized == "accessible_role" or normalized == "style_classes":
        selected = candidate_section == "mandatory"
        return PropertyPolicy("medium", selected, True, "assistive type evidence")

    if normalized == "ordinal":
        # The bridge only synthesizes ordinal after the stable identity remains
        # ambiguous, so an existing candidate ordinal is weak but necessary.
        return PropertyPolicy(
            "low",
            candidate_section in {"mandatory", "assistive"},
            True,
            "inferred final discriminator",
        )

    if normalized in _LOW_KEYS:
        selected = candidate_section == "mandatory"
        return PropertyPolicy("low", selected, True, "weak fallback discriminator")

    if normalized in _RUNTIME_KEYS:
        return PropertyPolicy("runtime", False, True, "runtime/state value")

    # Unknown scalar/list properties remain available to the author without
    # silently becoming identity.
    return PropertyPolicy("unknown", candidate_section == "mandatory", True, "unclassified evidence")


def available_properties(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten broadly useful JavaFX node evidence into matcher property keys."""
    result = {}
    for key in (
        "id",
        "class",
        "accessible_role",
        "accessible_text",
        "text",
        "user_data",
        "window",
        "visible",
        "disabled",
        "focused",
        "managed",
        "focus_traversable",
        "sibling_index",
        "sibling_count",
        "bounds",
        "style_classes",
        "hierarchy",
    ):
        value = payload.get(key)
        if value is not None and value != "":
            result[key] = value

    lineage = payload.get("stable_ancestors")
    if isinstance(lineage, (list, tuple)) and lineage:
        result["lineage"] = list(lineage)

    layout = payload.get("layout")
    if isinstance(layout, Mapping):
        for key, value in layout.items():
            if value is not None:
                result["layout.%s" % key] = value

    properties = payload.get("properties")
    if isinstance(properties, Mapping):
        for key, value in properties.items():
            if _property_value_supported(value):
                result["properties.%s" % key] = value

    # Keep process plumbing visible to diagnostics but never selectable.
    for key in ("ref", "capture_source"):
        value = payload.get(key)
        if value is not None:
            result[key] = value
    return result


def _property_value_supported(value: Any) -> bool:
    if value is None or isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, (list, tuple)):
        return all(_property_value_supported(item) for item in value)
    if isinstance(value, Mapping):
        return all(isinstance(key, str) and _property_value_supported(item) for key, item in value.items())
    return False
