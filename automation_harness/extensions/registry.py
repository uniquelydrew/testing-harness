"""Read-only registry for validated extension packages and declarative resources."""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .package import ExtensionFormatError, ExtensionPackage, matches_version

_URI = re.compile(r"ahext://([^/@]+)@([^/@]+)/(.+)")


class ExtensionRegistry:
    """Register package metadata; never import or execute bundled adapters."""

    def __init__(self, *, harness_version: str | None = None) -> None:
        if harness_version is None:
            from automation_harness import __version__
            harness_version = __version__
        self.harness_version = harness_version
        self._packages: dict[str, ExtensionPackage] = {}
        self._components: dict[str, tuple[str, str]] = {}
        self._behaviors: dict[str, tuple[str, str]] = {}

    def register(self, path: str | Path) -> ExtensionPackage:
        package = ExtensionPackage.open(path)
        self._check_compatibility(package)
        if package.extension_id in self._packages:
            raise ExtensionFormatError("extension already registered: %s" % package.extension_id)
        incoming_components = self._resource_ids(package, "components")
        incoming_behaviors = self._resource_ids(package, "behaviors")
        for name, paths, existing in (
            ("component", incoming_components, self._components),
            ("behavior", incoming_behaviors, self._behaviors),
        ):
            duplicates = set(paths) & set(existing)
            if duplicates:
                raise ExtensionFormatError("duplicate %s id: %s" % (name, sorted(duplicates)[0]))
        self._packages[package.extension_id] = package
        self._components.update({key: (package.extension_id, path) for key, path in incoming_components.items()})
        self._behaviors.update({key: (package.extension_id, path) for key, path in incoming_behaviors.items()})
        return package

    def _check_compatibility(self, package: ExtensionPackage) -> None:
        requirement = package.manifest.get("requires", {}).get("harness")
        if requirement and not matches_version(self.harness_version, requirement):
            raise ExtensionFormatError(
                "%s requires harness %s (installed %s)"
                % (package.extension_id, requirement, self.harness_version)
            )

    @staticmethod
    def _resource_ids(package: ExtensionPackage, category: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for path in package.manifest["resources"].get(category, []):
            value = package.read_json(path)
            identifier = value.get("id")
            if not isinstance(identifier, str) or not identifier.startswith(package.extension_id + "."):
                raise ExtensionFormatError(
                    "%s %s requires an id namespaced under %s" % (category, path, package.extension_id)
                )
            if identifier in result:
                raise ExtensionFormatError("duplicate %s resource id: %s" % (category, identifier))
            if category == "components":
                base_type = value.get("extends")
                from automation_harness.core.component_catalog import component_catalog
                if not isinstance(base_type, str) or base_type not in component_catalog()["types"]:
                    raise ExtensionFormatError("unknown canonical base type for %s: %r" % (identifier, base_type))
            result[identifier] = path
        return result

    def dependency_order(self) -> tuple[str, ...]:
        """Validate declared dependencies, versions and cycles before enabling any behavior."""
        visiting: set[str] = set()
        visited: set[str] = set()
        ordered: list[str] = []

        def visit(ext_id: str) -> None:
            if ext_id in visiting:
                raise ExtensionFormatError("cyclic extension dependency: %s" % ext_id)
            if ext_id in visited:
                return
            visiting.add(ext_id)
            for dependency in self._packages[ext_id].manifest.get("dependencies", []):
                dependency_id = dependency["id"]
                required_version = dependency["version"]
                present = self._packages.get(dependency_id)
                if present is None:
                    raise ExtensionFormatError("missing dependency %s for %s" % (dependency_id, ext_id))
                if not matches_version(present.version, required_version):
                    raise ExtensionFormatError(
                        "%s requires %s %s (registered %s)"
                        % (ext_id, dependency_id, required_version, present.version)
                    )
                visit(dependency_id)
            visiting.remove(ext_id)
            visited.add(ext_id)
            ordered.append(ext_id)

        for ext_id in sorted(self._packages):
            visit(ext_id)
        return tuple(ordered)

    def get_component(self, component_id: str) -> dict[str, Any]:
        self.dependency_order()
        ext_id, path = self._components[component_id]
        return deepcopy(self._packages[ext_id].read_json(path))

    def get_behavior(self, behavior_id: str) -> dict[str, Any]:
        self.dependency_order()
        ext_id, path = self._behaviors[behavior_id]
        return deepcopy(self._packages[ext_id].read_json(path))

    def read_asset(self, uri: str) -> bytes:
        self.dependency_order()
        match = _URI.fullmatch(uri)
        if match is None:
            raise ExtensionFormatError("invalid ahext asset URI")
        ext_id, version, path = match.groups()
        package = self._packages.get(ext_id)
        if package is None or package.version != version:
            raise ExtensionFormatError("asset references unavailable extension version")
        if path not in package.manifest["resources"].get("assets", []):
            raise ExtensionFormatError("asset not declared in extension")
        return package.read_bytes(path)

    @property
    def component_ids(self) -> tuple[str, ...]:
        """Namespaced, deterministic IDs of registered component profiles."""
        return tuple(sorted(self._components))

    def package_for_component(self, component_id: str) -> ExtensionPackage:
        """Return the owning versioned archive for a validated profile."""
        self.dependency_order()
        extension_id, _path = self._components[component_id]
        return self._packages[extension_id]

    @property
    def packages(self) -> tuple[ExtensionPackage, ...]:
        return tuple(self._packages[key] for key in sorted(self._packages))
