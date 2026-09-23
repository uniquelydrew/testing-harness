"""Explicit normalization of legacy Object Repositories for authoring.

Normalization is never performed merely by loading a repository. The planner
preserves immutable object IDs and locator strategies, marks known layout-only
containers as hidden authoring structure, reparents visible descendants around
those nodes, and proposes readable semantic aliases.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping

from automation_harness.core.component_naming import default_payload_name
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.repository_hierarchy import concrete_parent_ids
from automation_harness.models.gui import ObjectType


@dataclass(frozen=True)
class RepositoryNormalizationPlan:
    repository: ComponentRepository
    renames: Mapping[str, str]
    structural_only: tuple[str, ...]
    reparented: tuple[tuple[str, str | None, str | None], ...]
    unchanged: int = 0

    @property
    def changed(self) -> bool:
        return bool(self.renames or self.structural_only or self.reparented)


_ALWAYS_STRUCTURAL_NATIVE = (
    "jrootpane",
    "jlayeredpane",
    "glasspane",
    "hbox",
    "vbox",
    "anchorpane",
    "borderpane",
    "gridpane",
    "stackpane",
    "flowpane",
    "tilepane",
)

_LAYOUT_NATIVE = (
    "jpanel",
    "javafx.scene.layout.pane",
    "javafx.scene.layout.region",
)


def plan_repository_normalization(
    repository: ComponentRepository,
) -> RepositoryNormalizationPlan:
    """Build a deterministic, non-destructive semantic normalization plan."""
    parents = concrete_parent_ids(repository)
    by_id = {item.object_id: item for item in repository.components.values()}

    structural_ids = {
        definition.object_id
        for definition in repository.components.values()
        if _should_hide_structural_container(definition)
    }

    visible = [
        definition for definition in repository.components.values()
        if definition.object_id not in structural_ids
    ]
    hidden = [
        definition for definition in repository.components.values()
        if definition.object_id in structural_ids
    ]

    # Hidden definitions retain their existing aliases so any uncommon direct
    # references remain resolvable. Visible aliases are allocated around those
    # reserved names and each other.
    reserved = {definition.component_id for definition in hidden}
    name_by_id = {}
    renames = {}
    for definition in sorted(
        visible,
        key=lambda item: (item.object_type.value, item.component_id.casefold(), item.object_id),
    ):
        base = _semantic_name(definition)
        candidate = _unique_name(base, reserved)
        reserved.add(candidate)
        name_by_id[definition.object_id] = candidate
        if candidate != definition.component_id:
            renames[definition.component_id] = candidate

    final_definitions = {}
    reparented = []
    changed_ids = set()

    for definition in hidden:
        properties = dict(definition.properties or {})
        if properties.get("authoring_visibility") != "structural_only":
            properties["authoring_visibility"] = "structural_only"
            properties["structural_reason"] = "legacy_layout_container"
            definition = replace(
                definition,
                properties=properties,
                revision=definition.revision + 1,
            )
            changed_ids.add(definition.object_id)
        final_definitions[definition.object_id] = definition

    for original in visible:
        desired_owner = _nearest_visible_owner(
            original.object_id,
            parents,
            by_id,
            structural_ids,
        )
        current_owner = original.owner_object_id
        new_name = name_by_id[original.object_id]
        properties = dict(original.properties or {})
        properties.pop("authoring_visibility", None)
        properties.pop("structural_reason", None)

        changed = (
            new_name != original.component_id
            or desired_owner != current_owner
            or properties != dict(original.properties or {})
        )
        updated = replace(
            original,
            component_id=new_name,
            owner_object_id=desired_owner,
            properties=properties,
            revision=original.revision + 1 if changed else original.revision,
        )
        final_definitions[original.object_id] = updated
        if changed:
            changed_ids.add(original.object_id)
        if desired_owner != current_owner:
            old_parent = (
                by_id[current_owner].component_id
                if current_owner in by_id else None
            )
            new_parent_definition = final_definitions.get(desired_owner) or by_id.get(desired_owner)
            new_parent = (
                name_by_id.get(desired_owner)
                or (new_parent_definition.component_id if new_parent_definition is not None else None)
            )
            reparented.append((new_name, old_parent, new_parent))

    # Re-key by the normalized aliases only after every immutable relationship
    # has been resolved.
    normalized = ComponentRepository({
        definition.component_id: definition
        for definition in final_definitions.values()
    })

    structural_names = tuple(sorted(
        final_definitions[object_id].component_id
        for object_id in structural_ids
    ))
    unchanged = len(repository.components) - len(changed_ids)
    return RepositoryNormalizationPlan(
        repository=normalized,
        renames=dict(sorted(renames.items())),
        structural_only=structural_names,
        reparented=tuple(sorted(reparented)),
        unchanged=unchanged,
    )


def _nearest_visible_owner(
    object_id,
    parents,
    by_id,
    structural_ids,
):
    owner = parents.get(object_id)
    visited = set()
    while owner is not None:
        if owner in visited:
            return None
        visited.add(owner)
        if owner not in structural_ids:
            return owner if owner in by_id else None
        owner = parents.get(owner)
    return None


def _should_hide_structural_container(definition) -> bool:
    if definition.object_type != ObjectType.PANEL:
        return False
    native = str(definition.native_class or "").casefold()
    if any(token in native for token in _ALWAYS_STRUCTURAL_NATIVE):
        return True
    if not any(token in native for token in _LAYOUT_NATIVE):
        return False
    return not _has_meaningful_semantic_identity(definition)


def _has_meaningful_semantic_identity(definition) -> bool:
    for strategy in definition.strategies:
        options = strategy.options
        if not isinstance(options, Mapping):
            continue
        identity = options.get("identification", options)
        if not isinstance(identity, Mapping):
            continue
        mandatory = identity.get("mandatory", identity)
        assistive = identity.get("assistive", {})
        for source in (mandatory, assistive):
            if not isinstance(source, Mapping):
                continue
            for key in ("accessible_id", "id", "accessible_text", "text", "name"):
                value = source.get(key)
                if value in (None, ""):
                    continue
                text = str(value).strip()
                if text and not _looks_structural_name(text):
                    return True
    return False


def _looks_structural_name(value: str) -> bool:
    folded = value.casefold().replace(" ", "").replace("_", "").replace("-", "")
    if not folded:
        return True
    structural = (
        "panel", "pane", "contentpane", "rootpane", "glasspane",
        "hbox", "vbox", "container", "layout",
    )
    return any(folded == item or folded.startswith(item) for item in structural)


def _semantic_name(definition) -> str:
    payload = {
        "object_type": definition.object_type.value,
        "native_class": definition.native_class,
    }
    identity = _preferred_identity(definition)
    payload.update(identity)

    name = default_payload_name(payload)
    # If locator metadata cannot provide a useful label, reuse only the leaf of
    # the old alias as a final semantic hint. This deliberately drops all
    # qualified ancestry from the new author-facing name.
    generic = _generic_type_name(definition.object_type)
    if name == generic:
        leaf = definition.component_id.rsplit(".", 1)[-1]
        if leaf and leaf != definition.component_id or "." not in leaf:
            leaf_payload = {
                "object_type": definition.object_type.value,
                "name": leaf,
                "native_class": definition.native_class,
            }
            fallback = default_payload_name(leaf_payload)
            if fallback:
                name = fallback
    return name


def _preferred_identity(definition):
    result = {}
    for strategy in definition.strategies:
        options = strategy.options
        if not isinstance(options, Mapping):
            continue
        identity = options.get("identification", options)
        if not isinstance(identity, Mapping):
            continue
        mandatory = identity.get("mandatory", identity)
        assistive = identity.get("assistive", {})
        if isinstance(mandatory, Mapping):
            result.update(mandatory)
        if isinstance(assistive, Mapping):
            for key, value in assistive.items():
                result.setdefault(key, value)
        if result:
            break
    return result


def _generic_type_name(object_type):
    return default_payload_name({"object_type": object_type.value})


def _unique_name(base: str, reserved: set[str]) -> str:
    if base not in reserved:
        return base
    index = 2
    while "%s %d" % (base, index) in reserved:
        index += 1
    return "%s %d" % (base, index)
