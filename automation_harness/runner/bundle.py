from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import yaml


class BundleError(ValueError):
    pass


@dataclass(frozen=True)
class TestBundle:
    __test__ = False
    root: Path
    name: str
    version: int
    requires: frozenset[str]
    tests: tuple[Path, ...]
    manifest: dict[str, Any]
    components: Path | None = None
    step_libraries: tuple[Path, ...] = ()
    variables: dict[str, Any] | None = None
    target: dict[str, Any] | None = None

    @classmethod
    def load(cls, root: str | Path) -> "TestBundle":
        root = Path(root).expanduser().resolve()
        if not root.is_dir():
            raise BundleError(f"Bundle directory does not exist: {root}")
        manifest_path = root / "manifest.yaml"
        if not manifest_path.is_file():
            raise BundleError(f"Missing manifest.yaml in {root}")
        try:
            raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise BundleError(f"Invalid YAML in {manifest_path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise BundleError("manifest.yaml must contain a mapping")
        name = raw.get("name")
        version = raw.get("version", 1)
        requires = raw.get("requires", [])
        tests = raw.get("tests", [])
        if not isinstance(name, str) or not name.strip():
            raise BundleError("manifest.name must be a non-empty string")
        if not isinstance(version, int) or version < 1:
            raise BundleError("manifest.version must be a positive integer")
        if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
            raise BundleError("manifest.requires must be a list of strings")
        if not isinstance(tests, list) or not tests or not all(isinstance(item, str) for item in tests):
            raise BundleError("manifest.tests must be a non-empty list of relative paths")

        resolved_tests: list[Path] = []
        for relative in tests:
            candidate = _safe_relative(root, relative, label="Test")
            if not candidate.is_file():
                raise BundleError(f"Declared test does not exist: {relative}")
            resolved_tests.append(candidate)

        raw_step_libraries = raw.get("step_libraries", [])
        if not isinstance(raw_step_libraries, list) or not all(
            isinstance(item, str) and item.strip() for item in raw_step_libraries
        ):
            raise BundleError("manifest.step_libraries must be a list of non-empty relative paths")
        step_libraries: list[Path] = []
        for relative in raw_step_libraries:
            library = _safe_relative(root, relative, label="Step library")
            if not library.is_file():
                raise BundleError(f"Declared step library does not exist: {relative}")
            if library.suffix != ".py":
                raise BundleError(f"Step library must be a Python source file: {relative}")
            step_libraries.append(library)

        variables = raw.get("variables", {})
        if not isinstance(variables, dict) or not all(isinstance(key, str) and key.strip() for key in variables):
            raise BundleError("manifest.variables must be a mapping with non-empty string keys")
        invalid_variable_names = [key for key in variables if "." in key or key != key.strip()]
        if invalid_variable_names:
            raise BundleError("manifest.variables keys must be top-level names without '.': " + ", ".join(invalid_variable_names))
        try:
            json.dumps(variables)
        except (TypeError, ValueError) as exc:
            raise BundleError(f"manifest.variables must contain JSON-compatible values: {exc}") from exc

        components_value = raw.get("components")
        if components_value is None:
            conventional = root / "resources" / "components.yaml"
            components = conventional.resolve() if conventional.is_file() else None
        else:
            if not isinstance(components_value, str) or not components_value.strip():
                raise BundleError("manifest.components must be a non-empty relative path when supplied")
            components = _safe_relative(root, components_value, label="Component resource")
            if not components.is_file():
                raise BundleError(f"Declared component resource does not exist: {components_value}")

        target = raw.get("target")
        if target is not None:
            if not isinstance(target, dict) or not isinstance(target.get("kind"), str):
                raise BundleError("manifest.target must be a mapping with a string kind")
            if target["kind"] == "gtk-demo":
                example = target.get("example")
                if not isinstance(example, str) or not example.strip():
                    raise BundleError("GTK Demo target requires a non-empty target.example")
            elif target["kind"] == "java-desktop":
                command = target.get("command")
                if not isinstance(command, list) or not command or not all(isinstance(item, str) and item for item in command):
                    raise BundleError("Java desktop target requires target.command as a non-empty command list")
                for key in ("working_directory", "expected_application"):
                    if key in target and (not isinstance(target[key], str) or not target[key].strip()):
                        raise BundleError(f"Java desktop target.{key} must be a non-empty string when supplied")
                if "environment" in target and (
                    not isinstance(target["environment"], dict)
                    or not all(isinstance(key, str) and isinstance(value, str) for key, value in target["environment"].items())
                ):
                    raise BundleError("Java desktop target.environment must be a string-to-string mapping")
                if "startup_timeout" in target and (
                    not isinstance(target["startup_timeout"], (int, float))
                    or isinstance(target["startup_timeout"], bool)
                    or target["startup_timeout"] <= 0
                ):
                    raise BundleError("Java desktop target.startup_timeout must be a positive number")
            elif target["kind"] not in {"reference"}:
                raise BundleError(f"unsupported manifest.target kind: {target['kind']!r}")

        return cls(
            root=root,
            name=name.strip(),
            version=version,
            requires=frozenset(requires),
            tests=tuple(resolved_tests),
            manifest=raw,
            components=components,
            step_libraries=tuple(step_libraries),
            variables=variables,
            target=dict(target) if target is not None else None,
        )


def _safe_relative(root: Path, relative: str, *, label: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BundleError(f"{label} path escapes bundle root: {relative}") from exc
    return candidate
