from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

import yaml

from automation_harness.models.component import ComponentDefinition, ComponentStrategy


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
            if not isinstance(raw, dict):
                raise ComponentRepositoryError(f"{path}: root must be a mapping")
            version = raw.get("version", 1)
            if version != 1:
                raise ComponentRepositoryError(f"{path}: unsupported component schema version {version!r}")
            entries = raw.get("components", {})
            if not isinstance(entries, dict):
                raise ComponentRepositoryError(f"{path}: components must be a mapping")
            for component_id, value in entries.items():
                merged[str(component_id)] = _parse_component(path, str(component_id), value)
        return cls(merged)

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
            "version": 1,
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


def _parse_component(path: Path, component_id: str, value: Any) -> ComponentDefinition:
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

    raw_actions = value.get("actions", ["resolve", "activate"])
    if not isinstance(raw_actions, list) or not raw_actions or not all(isinstance(item, str) and item for item in raw_actions):
        raise ComponentRepositoryError(
            f"{path}: component {component_id!r}.actions must be a non-empty list of strings"
        )
    actions = frozenset(raw_actions)
    if "resolve" not in actions:
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
        options = {k: v for k, v in raw.items() if k != "type"}
        if strategy_type == "atspi":
            options = _normalize_atspi_strategy(path, component_id, index, options)
        strategies.append(ComponentStrategy(strategy_type, options))
    return ComponentDefinition(
        component_id=component_id,
        description=description,
        strategies=tuple(strategies),
        actions=actions,
        expected_states=dict(expected_states),
        revision=revision,
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
    return payload
