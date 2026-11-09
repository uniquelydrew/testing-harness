"""Explicit, data-only binding between archived extension profiles and objects.

This module never imports package-supplied modules or executes adapters. Existing
ObjectType, strategies, and immutable object_id remain authoritative. A binding
only adds a pinned declarative interpretation for authoring and observation UI.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, TYPE_CHECKING

from .package import ExtensionFormatError
from .registry import ExtensionRegistry

if TYPE_CHECKING:
    from automation_harness.models.component import ComponentDefinition


BINDING_KEY = "automation_harness.extension"
PACKAGES_ENV = "AUTOMATION_HARNESS_EXTENSIONS"


@dataclass(frozen=True)
class ExtensionBinding:
    component_id: str
    version: str

    @classmethod
    def from_definition(cls, definition: ComponentDefinition) -> ExtensionBinding | None:
        raw = definition.properties.get(BINDING_KEY)
        if raw is None:
            return None
        if not isinstance(raw, Mapping) or set(raw) != {"component_id", "version"}:
            raise ExtensionFormatError("invalid component extension binding on %s" % definition.component_id)
        component_id, version = raw["component_id"], raw["version"]
        if not isinstance(component_id, str) or not isinstance(version, str):
            raise ExtensionFormatError("component extension binding requires string id and version")
        return cls(component_id, version)


def load_registry_from_environment(*, harness_version: str | None = None) -> ExtensionRegistry | None:
    """Load only explicitly named archives, with no code execution or scanning.

    The caller owns the returned registry. There is no mutable process-wide
    registry and no implicit discovery in user-writable directories.
    """
    raw = os.environ.get(PACKAGES_ENV, "")
    if not raw:
        return None
    paths = raw.split(os.pathsep)
    if any(not path.strip() for path in paths):
        raise ExtensionFormatError("%s contains an empty package path" % PACKAGES_ENV)
    registry = ExtensionRegistry(harness_version=harness_version)
    for path in paths:
        registry.register(Path(path).expanduser())
    registry.dependency_order()
    return registry


def _profile_matches(definition: ComponentDefinition, profile: Mapping[str, Any]) -> bool:
    if profile.get("extends") != definition.object_type.value:
        return False
    required_framework = profile.get("framework")
    return not required_framework or required_framework == definition.framework


def available_profiles(
    definition: ComponentDefinition,
    registry: ExtensionRegistry | None,
) -> tuple[dict[str, Any], ...]:
    """Return compatible specialization choices; do not infer a binding."""
    if registry is None:
        return ()
    registry.dependency_order()
    return tuple(
        registry.get_component(component_id)
        for component_id in registry.component_ids
        if _profile_matches(definition, registry.get_component(component_id))
    )


def bind_extension(
    definition: ComponentDefinition,
    component_id: str,
    registry: ExtensionRegistry,
) -> ComponentDefinition:
    """Attach a version-pinned profile without changing UUID, type, or locator."""
    profile = registry.get_component(component_id)
    if not _profile_matches(definition, profile):
        raise ExtensionFormatError(
            "extension component %s is incompatible with %s (%s/%s)"
            % (component_id, definition.component_id, definition.object_type.value, definition.framework)
        )
    owner = registry.package_for_component(component_id)
    binding = {"component_id": component_id, "version": owner.version}
    properties = dict(definition.properties)
    if properties.get(BINDING_KEY) == binding:
        return definition
    properties[BINDING_KEY] = binding
    return replace(definition, properties=properties, revision=definition.revision + 1)


def unbind_extension(definition: ComponentDefinition) -> ComponentDefinition:
    if BINDING_KEY not in definition.properties:
        return definition
    properties = dict(definition.properties)
    properties.pop(BINDING_KEY)
    return replace(definition, properties=properties, revision=definition.revision + 1)


def resolve_extension(
    definition: ComponentDefinition,
    registry: ExtensionRegistry | None,
) -> dict[str, Any] | None:
    """Fail closed for missing, changed or incompatible opt-in packages."""
    binding = ExtensionBinding.from_definition(definition)
    if binding is None:
        return None
    if registry is None:
        raise ExtensionFormatError(
            "object %s requires extension %s@%s; configure %s"
            % (definition.component_id, binding.component_id, binding.version, PACKAGES_ENV)
        )
    try:
        owner = registry.package_for_component(binding.component_id)
        profile = registry.get_component(binding.component_id)
    except KeyError as exc:
        raise ExtensionFormatError("missing extension profile: %s" % binding.component_id) from exc
    if owner.version != binding.version:
        raise ExtensionFormatError(
            "extension version mismatch for %s: requires %s, loaded %s"
            % (binding.component_id, binding.version, owner.version)
        )
    if not _profile_matches(definition, profile):
        raise ExtensionFormatError("persisted extension binding is incompatible with the component")
    return profile


def component_options(
    definition: ComponentDefinition,
    registry: ExtensionRegistry | None,
) -> dict[str, Any] | None:
    """Authoring-facing view of packaged fields/options, never historical values."""
    profile = resolve_extension(definition, registry)
    if profile is None:
        return None
    fields = profile.get("fields", [])
    if not isinstance(fields, list) or not all(isinstance(field, dict) for field in fields):
        raise ExtensionFormatError("extension fields must be a list of objects")
    return {
        "id": profile["id"],
        "name": profile.get("display_name", profile["id"]),
        "capabilities": tuple(profile.get("capabilities", ())),
        "fields": tuple(dict(field) for field in fields),
        "read_only": profile.get("interaction", {}).get("selection") is False,
        "observation": dict(profile.get("observation", {})),
    }
