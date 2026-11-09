"""Data-only, versioned Automation Harness extension archives (.ahext)."""

from .package import ExtensionFormatError, ExtensionPackage, build_extension, matches_version
from .registry import ExtensionRegistry

__all__ = (
    "ExtensionFormatError", "ExtensionPackage", "ExtensionRegistry",
    "build_extension", "matches_version",
)

from .runtime import (
    BINDING_KEY, PACKAGES_ENV, ExtensionBinding, available_profiles,
    bind_extension, component_options, load_registry_from_environment,
    resolve_extension, unbind_extension,
)
__all__ += (
    "BINDING_KEY", "PACKAGES_ENV", "ExtensionBinding", "available_profiles",
    "bind_extension", "component_options", "load_registry_from_environment",
    "resolve_extension", "unbind_extension",
)
