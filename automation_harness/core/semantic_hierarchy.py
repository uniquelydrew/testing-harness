"""User-facing semantic ownership derived from real captured ancestors.

Native ancestry remains untouched in locator/scope metadata. This module creates
only resolvable canonical containers for Object Repository presentation.
"""
from __future__ import annotations

from dataclasses import replace
import re
from typing import Any, Mapping

from automation_harness.core.component_catalog import display_name
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType, classify_accessibility


_PRIMARY_CONTAINERS = frozenset({
    ObjectType.WINDOW,
    ObjectType.DIALOG,
    ObjectType.TAB_CONTAINER,
    ObjectType.TAB,
    ObjectType.TOOLBAR,
    ObjectType.MENU_BAR,
    ObjectType.MENU,
    ObjectType.CONTEXT_MENU,
})


def semantic_ancestor_descriptors(capture) -> tuple[dict[str, Any], ...]:
    properties = dict(getattr(capture, "backend_properties", {}) or {})
    raw = properties.get("semantic_ancestors")
    source = "semantic_ancestors"
    if not isinstance(raw, (list, tuple)):
        raw = properties.get("stable_ancestors")
        source = "stable_ancestors"
    if not isinstance(raw, (list, tuple)):
        raw = ()

    framework = str(getattr(capture, "framework", "") or "")
    window = getattr(capture, "window", None) or getattr(capture, "application", None)
    result = []
    for index, value in enumerate(raw):
        if not isinstance(value, Mapping):
            continue
        descriptor = _normalize_descriptor(value, framework=framework, window=window)
        if descriptor is None:
            continue

        # JavaFX Window is not a Node. The outermost stable scene-root Node is
        # nevertheless a real resolvable/highlightable object and serves as the
        # logical Window proxy instead of inventing a synthetic parent.
        if (
            source == "stable_ancestors"
            and index == 0
            and window
            and not any(item.get("object_type") in {"window", "dialog"} for item in result)
        ):
            descriptor = {
                **descriptor,
                "object_type": ObjectType.WINDOW.value,
                "name": str(window),
                "window_proxy": True,
            }

        object_type = _object_type(descriptor)
        if object_type in _PRIMARY_CONTAINERS:
            descriptor["object_type"] = object_type.value
            result.append(descriptor)
            continue

        # Panel is deliberately a fallback: only a genuinely named/identified
        # real container survives, never generic HBox/VBox/JPanel ancestry.
        if object_type == ObjectType.PANEL and _meaningful_panel(descriptor):
            descriptor["object_type"] = ObjectType.PANEL.value
            result.append(descriptor)

    # Never expose duplicate adjacent descriptors for the same real object.
    deduped = []
    for descriptor in result:
        identity = _descriptor_identity_key(descriptor)
        if deduped and _descriptor_identity_key(deduped[-1]) == identity:
            continue
        deduped.append(descriptor)
    return tuple(deduped)


def materialize_semantic_ancestors(repository, capture):
    """Return repository, nearest semantic owner object_id, and created names."""
    owner_object_id = None
    created = []
    for descriptor in semantic_ancestor_descriptors(capture):
        definition = _definition_from_descriptor(
            repository,
            descriptor,
            owner_object_id=owner_object_id,
        )
        if definition is None:
            continue
        existing = _find_existing(repository, definition)
        if existing is None:
            repository = repository.with_component(definition)
            existing = definition
            created.append(definition.component_id)
        elif owner_object_id is not None and existing.owner_object_id is None:
            existing = replace(
                existing,
                owner_object_id=owner_object_id,
                revision=existing.revision + 1,
            )
            repository = repository.with_component(existing)
        owner_object_id = existing.object_id
    return repository, owner_object_id, tuple(created)


def _normalize_descriptor(value: Mapping[str, Any], *, framework: str, window):
    native_class = value.get("native_class") or value.get("class")
    role = value.get("role") or value.get("accessible_role")
    name = (
        value.get("name")
        or value.get("accessible_text")
        or value.get("text")
    )
    accessible_id = value.get("accessible_id") or value.get("id")
    descriptor = {
        "framework": value.get("framework") or framework,
        "native_class": native_class,
        "role": role,
        "name": name,
        "accessible_id": accessible_id,
        "component_path": value.get("component_path"),
        "window": value.get("window") or window,
        "application": value.get("application"),
        "object_type": value.get("object_type"),
        "window_proxy": bool(value.get("window_proxy")),
    }
    return {
        key: item for key, item in descriptor.items()
        if item not in (None, "")
    }


def _object_type(descriptor: Mapping[str, Any]) -> ObjectType:
    raw = descriptor.get("object_type")
    if raw not in (None, ""):
        try:
            return ObjectType(str(raw))
        except ValueError:
            pass
    return classify_accessibility(
        str(descriptor.get("role") or ""),
        str(descriptor.get("native_class") or ""),
    )


def _meaningful_panel(descriptor: Mapping[str, Any]) -> bool:
    name = str(descriptor.get("name") or "").strip()
    accessible_id = str(descriptor.get("accessible_id") or "").strip()
    native = str(descriptor.get("native_class") or "").casefold()
    if not (name or accessible_id):
        return False
    if any(token in native for token in (
        "hbox", "vbox", "anchorpane", "borderpane", "gridpane",
        "stackpane", "flowpane", "tilepane", "jrootpane",
        "jlayeredpane",
    )):
        return False
    folded = (name or accessible_id).casefold().replace(" ", "")
    return folded not in {"contentpane", "glasspane", "rootpane"}


def _definition_from_descriptor(repository, descriptor, *, owner_object_id):
    object_type = _object_type(descriptor)
    framework = str(descriptor.get("framework") or "")
    strategy = _strategy(descriptor, framework)
    if strategy is None:
        return None
    component_id = _unique_name(repository, descriptor, object_type)
    actions = {"resolve"}
    if object_type in {ObjectType.WINDOW, ObjectType.DIALOG, ObjectType.TAB_CONTAINER, ObjectType.TAB, ObjectType.TOOLBAR}:
        actions.add("focus")
    if object_type in {ObjectType.WINDOW, ObjectType.DIALOG, ObjectType.TAB}:
        actions.add("activate")
    if object_type in {ObjectType.MENU_BAR, ObjectType.MENU, ObjectType.CONTEXT_MENU}:
        actions.add("select_menu_item")
    if object_type == ObjectType.MENU:
        actions.add("click")
    return ComponentDefinition(
        component_id=component_id,
        description="Semantic %s container" % display_name(object_type),
        strategies=(strategy,),
        actions=frozenset(actions),
        object_type=object_type,
        properties={
            "semantic_container": True,
            **({"window_proxy": True} if descriptor.get("window_proxy") else {}),
        },
        framework=framework or None,
        native_class=descriptor.get("native_class"),
        owner_object_id=owner_object_id,
    )


def _strategy(descriptor: Mapping[str, Any], framework: str):
    accessible_id = descriptor.get("accessible_id")
    name = descriptor.get("name")
    role = descriptor.get("role")
    native_class = descriptor.get("native_class")
    window = descriptor.get("window")
    component_path = descriptor.get("component_path")

    if framework == "javafx":
        mandatory = {}
        if accessible_id:
            mandatory["id"] = accessible_id
        elif name:
            mandatory["accessible_text"] = name
        elif native_class:
            mandatory["class"] = native_class
        if not mandatory:
            return None
        assistive = {}
        if native_class and "class" not in mandatory:
            assistive["class"] = native_class
        if role:
            assistive["accessible_role"] = role
        if window:
            assistive["window"] = window
        identity = {"mandatory": mandatory}
        if assistive:
            identity["assistive"] = assistive
        return ComponentStrategy("javafx", {"identification": identity})

    if framework in {"swing", "awt", "java", "jogl"}:
        mandatory = {}
        if accessible_id:
            mandatory["accessible_id"] = accessible_id
        elif name:
            mandatory["name"] = name
        if native_class:
            mandatory["native_class"] = native_class
        if not mandatory:
            return None
        assistive = {}
        if component_path:
            assistive["component_path"] = component_path
        if window:
            assistive["window"] = window
        identity = {"mandatory": mandatory}
        if assistive:
            identity["assistive"] = assistive
        return ComponentStrategy("java_agent", {"identification": identity})

    mandatory = {}
    if accessible_id:
        mandatory["accessible_id"] = accessible_id
        if role:
            mandatory["role"] = role
    elif name:
        mandatory["name"] = name
        if role:
            mandatory["role"] = role
    elif role and window:
        mandatory["role"] = role
    if not mandatory:
        return None
    assistive = {}
    if window:
        assistive["window"] = window
    if descriptor.get("application"):
        assistive["application"] = descriptor["application"]
    identity = {"mandatory": mandatory}
    if assistive:
        identity["assistive"] = assistive
    return ComponentStrategy(
        "java_accessibility" if framework == "java_accessibility" else "atspi",
        {"identification": identity},
    )


def _find_existing(repository, proposed):
    for definition in repository.components.values():
        if definition.object_type != proposed.object_type:
            continue
        if any(strategy in definition.strategies for strategy in proposed.strategies):
            return definition
    return None


def _unique_name(repository, descriptor, object_type):
    type_name = display_name(object_type)
    raw = descriptor.get("name") or descriptor.get("accessible_id") or ""
    text = _readable_name(str(raw).strip())
    if not text:
        text = type_name
    elif not _contains_type_suffix(text, type_name):
        text = "%s %s" % (text, type_name)
    candidate = text
    index = 2
    while candidate in repository.components:
        candidate = "%s %d" % (text, index)
        index += 1
    return candidate


def _readable_name(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", value)
    value = value.replace("_", " ").replace("-", " ")
    value = re.sub(r"\s+", " ", value).strip()
    if not value:
        return ""
    # Preserve intentional acronyms such as ``MSCT`` while making identifiers
    # such as ``mainTabs`` read like the rest of the semantic repository.
    return value[:1].upper() + value[1:]


def _contains_type_suffix(text: str, type_name: str) -> bool:
    folded = text.casefold()
    type_folded = type_name.casefold()
    if folded == type_folded or folded.endswith(" " + type_folded):
        return True
    if type_folded == "tabs" and folded.endswith(" tabs"):
        return True
    return False


def _descriptor_identity_key(descriptor):
    return (
        descriptor.get("framework"),
        descriptor.get("accessible_id"),
        descriptor.get("name"),
        descriptor.get("native_class"),
        descriptor.get("component_path"),
        descriptor.get("window"),
    )
