from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType, default_actions


class ComponentRepositoryError(ValueError):
    pass


_ATSPI_SIMPLE_KEYS = {"name", "role", "accessible_id", "application", "window", "hierarchy"}
_ATSPI_PARENT_KEYS = {"name", "role", "accessible_id"}


@dataclass(frozen=True)
class ComponentRepository:
    components: dict[str, ComponentDefinition]

    @classmethod
    def load(cls, paths: Iterable[Path]) -> "ComponentRepository":
        merged: dict[str, ComponentDefinition] = {}
        for path in paths:
            if not path.is_file():
                continue
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ComponentRepositoryError(f"invalid YAML in {path}: {exc}") from exc
            repository = cls.from_document(raw, source=str(path))
            merged.update(repository.components)
        return cls(merged)

    @classmethod
    def from_document(cls, raw: Any, *, source: str = "repository") -> "ComponentRepository":
        """Parse a repository document supplied by an editor or a YAML file."""
        if not isinstance(raw, dict):
            raise ComponentRepositoryError(f"{source}: root must be a mapping")
        version = raw.get("version", 1)
        if version not in {1, 2}:
            raise ComponentRepositoryError(f"{source}: unsupported component schema version {version!r}")
        entries = raw.get("components", {})
        if not isinstance(entries, dict):
            raise ComponentRepositoryError(f"{source}: components must be a mapping")
        return cls({
            str(component_id): _parse_component(Path(source), str(component_id), value, version=version)
            for component_id, value in entries.items()
        })

    def get(self, component_id: str) -> ComponentDefinition:
        try:
            return self.components[component_id]
        except KeyError as exc:
            candidates = self.suggest(component_id)
            suffix = f"; possible matches: {', '.join(candidates)}" if candidates else ""
            raise ComponentRepositoryError(f"unknown component {component_id!r}{suffix}") from exc

    def contains(self, component_id: str) -> bool:
        return component_id in self.components

    def suggest(self, component_id: str, *, limit: int = 3) -> list[str]:
        from difflib import get_close_matches

        return get_close_matches(component_id, self.components.keys(), n=limit, cutoff=0.45)

    def to_document(self) -> dict[str, Any]:
        return {
            "version": 2,
            "components": {
                component_id: _component_to_mapping(definition)
                for component_id, definition in sorted(self.components.items())
            },
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump(self.to_document(), sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    def with_component(self, definition: ComponentDefinition) -> "ComponentRepository":
        merged = dict(self.components)
        merged[definition.component_id] = definition
        return ComponentRepository(merged)

    def without_component(self, component_id: str) -> "ComponentRepository":
        merged = dict(self.components)
        merged.pop(component_id, None)
        return ComponentRepository(merged)

    def overlay(self, other: "ComponentRepository") -> "ComponentRepository":
        merged = dict(self.components)
        merged.update(other.components)
        return ComponentRepository(merged)


def _parse_component(path: Path, component_id: str, value: Any, *, version: int = 1) -> ComponentDefinition:
    if not isinstance(value, dict):
        raise ComponentRepositoryError(f"{path}: component {component_id!r} must be a mapping")
    description = value.get("description", "")
    if not isinstance(description, str):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.description must be a string")
    revision = value.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.revision must be a positive integer")
    expected_states = value.get("expected_states", {})
    if not isinstance(expected_states, Mapping):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.expected_states must be a mapping")
    visual = _normalize_visual(path, component_id, value.get("visual"))

    raw_actions = value.get("actions")
    raw_object_type = value.get("object_type")
    if raw_object_type is None:
        object_type = ObjectType.CUSTOM
    else:
        try:
            object_type = ObjectType(str(raw_object_type))
        except ValueError as exc:
            raise ComponentRepositoryError(f"{path}: component {component_id!r}.object_type is not a known semantic type") from exc
    if raw_actions is None:
        raw_actions = [item.value for item in default_actions(object_type)] if version == 2 else ["resolve", "activate"]
    if not isinstance(raw_actions, list) or not raw_actions or not all(isinstance(item, str) and item for item in raw_actions):
        raise ComponentRepositoryError(
            f"{path}: component {component_id!r}.actions must be a non-empty list of strings"
        )
    actions = frozenset(raw_actions)
    if version == 1 and "resolve" not in actions:
        raise ComponentRepositoryError(f"{path}: component {component_id!r} must support the resolve action")

    raw_strategies = value.get("strategies", [])
    if not isinstance(raw_strategies, list) or not raw_strategies:
        raise ComponentRepositoryError(f"{path}: component {component_id!r} requires at least one strategy")
    strategies: list[ComponentStrategy] = []
    for index, raw in enumerate(raw_strategies):
        if not isinstance(raw, dict):
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r}.strategies[{index}] must be a mapping"
            )
        strategy_type = raw.get("type")
        if not isinstance(strategy_type, str) or not strategy_type:
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r}.strategies[{index}].type must be a non-empty string"
            )
        if strategy_type == "reference":
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r} uses removed strategy 'reference'; "
                "use 'reference_inspection' only for non-interactive synthetic inspection"
            )
        if strategy_type == "reference_inspection" and "activate" in actions:
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r} cannot declare activate with reference_inspection; "
                "synthetic inspection may locate evidence but may not perform UI interaction"
            )
        if strategy_type == "anchored_visual" and "activate" in actions:
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r} cannot declare activate with anchored_visual; "
                "visual targets are externally resolved and read-only"
            )
        options = {k: v for k, v in raw.items() if k != "type"}
        if strategy_type in {"atspi", "java_accessibility"}:
            options = _normalize_atspi_strategy(path, component_id, index, options)
        elif strategy_type == "anchored_visual":
            options = _normalize_anchored_visual_strategy(path, component_id, index, options)
        strategies.append(ComponentStrategy(strategy_type, options))
    properties = value.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.properties must be a mapping")
    framework = value.get("framework")
    native_class = value.get("native_class")
    if framework is not None and not isinstance(framework, str):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.framework must be a string")
    if native_class is not None and not isinstance(native_class, str):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.native_class must be a string")
    subobjects = value.get("subobjects", {})
    if not isinstance(subobjects, Mapping) or not all(isinstance(key, str) and isinstance(item, Mapping) for key, item in subobjects.items()):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.subobjects must map IDs to selector mappings")
    return ComponentDefinition(
        component_id=component_id,
        description=description,
        strategies=tuple(strategies),
        actions=actions,
        expected_states=dict(expected_states),
        revision=revision,
        visual=visual,
        repository_path=path if path.name not in {"repository", "editor"} else None,
        object_type=object_type,
        properties=dict(properties),
        framework=framework,
        native_class=native_class,
        subobjects={str(key): dict(item) for key, item in subobjects.items()},
    )


def _normalize_atspi_strategy(
    path: Path,
    component_id: str,
    index: int,
    options: Mapping[str, Any],
) -> dict[str, Any]:
    prefix = f"{path}: component {component_id!r}.strategies[{index}]"
    if "identification" in options:
        if len(options) != 1:
            extra = sorted(set(options) - {"identification"})
            raise ComponentRepositoryError(
                f"{prefix}: nested AT-SPI identification cannot be mixed with flat properties: {', '.join(extra)}"
            )
        raw_identity = options["identification"]
    else:
        # Legacy flat locators are read for compatibility but normalized into
        # the richer identity model on load/save.
        raw_identity = {"mandatory": dict(options)}

    if not isinstance(raw_identity, Mapping):
        raise ComponentRepositoryError(f"{prefix}.identification must be a mapping")
    mandatory = raw_identity.get("mandatory", {})
    assistive = raw_identity.get("assistive", {})
    ordinal = raw_identity.get("ordinal")
    if not isinstance(mandatory, Mapping) or not mandatory:
        raise ComponentRepositoryError(f"{prefix}.identification.mandatory must be a non-empty mapping")
    if not isinstance(assistive, Mapping):
        raise ComponentRepositoryError(f"{prefix}.identification.assistive must be a mapping")
    _validate_locator_conditions(prefix + ".identification.mandatory", mandatory)
    _validate_locator_conditions(prefix + ".identification.assistive", assistive)

    if isinstance(ordinal, Mapping):
        if set(ordinal) != {"index"}:
            raise ComponentRepositoryError(f"{prefix}.identification.ordinal mapping must contain only 'index'")
        ordinal = ordinal.get("index")
    if ordinal is not None and (not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0):
        raise ComponentRepositoryError(f"{prefix}.identification.ordinal must be a non-negative integer")

    identity: dict[str, Any] = {"mandatory": dict(mandatory)}
    if assistive:
        identity["assistive"] = dict(assistive)
    if ordinal is not None:
        identity["ordinal"] = ordinal
    return {"identification": identity}


def _normalize_anchored_visual_strategy(
    path: Path, component_id: str, index: int, options: Mapping[str, Any],
) -> dict[str, Any]:
    prefix = f"{path}: component {component_id!r}.strategies[{index}]"
    if set(options) != {"anchor_identification", "relative_bounds"}:
        raise ComponentRepositoryError(
            f"{prefix} must contain only anchor_identification and relative_bounds"
        )
    normalized_anchor = _normalize_atspi_strategy(
        path, component_id, index, {"identification": options["anchor_identification"]},
    )["identification"]
    relative = options["relative_bounds"]
    if not isinstance(relative, list) or len(relative) != 4 or any(
        not isinstance(value, (int, float)) or isinstance(value, bool) for value in relative
    ):
        raise ComponentRepositoryError(f"{prefix}.relative_bounds must be four numbers")
    rx, ry, rw, rh = (float(value) for value in relative)
    if min(rx, ry, rw, rh) < 0 or rw <= 0 or rh <= 0 or rx + rw > 1.0001 or ry + rh > 1.0001:
        raise ComponentRepositoryError(
            f"{prefix}.relative_bounds must be positive normalized coordinates within the anchor"
        )
    return {"anchor_identification": normalized_anchor, "relative_bounds": [rx, ry, rw, rh]}


def _validate_locator_conditions(prefix: str, conditions: Mapping[str, Any]) -> None:
    for key, value in conditions.items():
        if key in _ATSPI_SIMPLE_KEYS:
            if key == "hierarchy":
                if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                    raise ComponentRepositoryError(f"{prefix}.hierarchy must be a non-empty list of strings")
            elif not isinstance(value, str) or not value:
                raise ComponentRepositoryError(f"{prefix}.{key} must be a non-empty string")
            continue
        if key == "parent":
            if not isinstance(value, Mapping) or not value:
                raise ComponentRepositoryError(f"{prefix}.parent must be a non-empty mapping")
            unknown = set(value) - _ATSPI_PARENT_KEYS
            if unknown:
                raise ComponentRepositoryError(
                    f"{prefix}.parent contains unsupported properties: {', '.join(sorted(unknown))}"
                )
            for parent_key, parent_value in value.items():
                if not isinstance(parent_value, str) or not parent_value:
                    raise ComponentRepositoryError(f"{prefix}.parent.{parent_key} must be a non-empty string")
            continue
        if key.startswith("attribute:") and len(key) > len("attribute:"):
            if not isinstance(value, str):
                raise ComponentRepositoryError(f"{prefix}.{key} must be a string")
            continue
        raise ComponentRepositoryError(f"{prefix} contains unsupported AT-SPI property {key!r}")


def _component_to_mapping(definition: ComponentDefinition) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "description": definition.description,
        "revision": definition.revision,
        "actions": sorted(definition.actions),
        "strategies": [
            {"type": strategy.type, **dict(strategy.options)}
            for strategy in definition.strategies
        ],
    }
    if definition.expected_states:
        payload["expected_states"] = dict(definition.expected_states)
    if definition.visual:
        payload["visual"] = dict(definition.visual)
    payload["object_type"] = definition.object_type.value
    if definition.properties:
        payload["properties"] = dict(definition.properties)
    if definition.framework:
        payload["framework"] = definition.framework
    if definition.native_class:
        payload["native_class"] = definition.native_class
    if definition.subobjects:
        payload["subobjects"] = {key: dict(value) for key, value in definition.subobjects.items()}
    return payload


def _normalize_visual(path: Path, component_id: str, value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    prefix = f"{path}: component {component_id!r}.visual"
    if not isinstance(value, Mapping):
        raise ComponentRepositoryError(f"{prefix} must be a mapping")
    if value.get("bounds", "component") != "component":
        raise ComponentRepositoryError(f"{prefix}.bounds must be 'component'")
    revision = value.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ComponentRepositoryError(f"{prefix}.revision must be a non-negative integer")
    variants = value.get("variants", {})
    if not isinstance(variants, Mapping):
        raise ComponentRepositoryError(f"{prefix}.variants must be a mapping")
    normalized: dict[str, Any] = {"bounds": "component", "revision": revision, "variants": {}}
    for key, raw in variants.items():
        if not isinstance(key, str) or not key or not all(ch.isalnum() or ch in "-_" for ch in key):
            raise ComponentRepositoryError(f"{prefix}.variants keys must be safe non-empty identifiers")
        if not isinstance(raw, Mapping):
            raise ComponentRepositoryError(f"{prefix}.variants.{key} must be a mapping")
        image = raw.get("image")
        if not isinstance(image, str) or not image:
            raise ComponentRepositoryError(f"{prefix}.variants.{key}.image must be a non-empty relative path")
        _validate_visual_path(prefix, image)
        item: dict[str, Any] = {"image": image}
        mask = raw.get("mask")
        if mask is not None:
            if not isinstance(mask, str) or not mask:
                raise ComponentRepositoryError(f"{prefix}.variants.{key}.mask must be a relative path")
            _validate_visual_path(prefix, mask)
            item["mask"] = mask
        profile = raw.get("profile")
        if not isinstance(profile, Mapping) or not profile or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in profile.items()):
            raise ComponentRepositoryError(f"{prefix}.variants.{key}.profile must be a non-empty string mapping")
        item["profile"] = dict(profile)
        component_revision = raw.get("component_revision")
        if not isinstance(component_revision, int) or isinstance(component_revision, bool) or component_revision < 1:
            raise ComponentRepositoryError(f"{prefix}.variants.{key}.component_revision must be a positive integer")
        item["component_revision"] = component_revision
        for field, default in (("pixel_tolerance", 12), ("max_difference_ratio", 0.01)):
            field_value = raw.get(field, default)
            if not isinstance(field_value, (int, float)) or isinstance(field_value, bool) or field_value < 0 or (field == "max_difference_ratio" and field_value > 1):
                raise ComponentRepositoryError(f"{prefix}.variants.{key}.{field} is invalid")
            item[field] = field_value
        normalized["variants"][key] = item
    return normalized


def _validate_visual_path(prefix: str, value: str) -> None:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts or candidate.parts[0] != "visual":
        raise ComponentRepositoryError(f"{prefix}: visual assets must be relative paths under visual/")
