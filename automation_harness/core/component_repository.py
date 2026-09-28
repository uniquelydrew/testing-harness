from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import os
import tempfile
from typing import Any, Iterable, Mapping
from uuid import UUID, uuid5, NAMESPACE_URL

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

    def __post_init__(self) -> None:
        seen: dict[str, str] = {}
        for name, definition in self.components.items():
            if name != definition.component_id:
                raise ComponentRepositoryError(
                    f"component repository key {name!r} does not match definition name {definition.component_id!r}"
                )
            object_id = _normalize_object_id(definition.object_id, prefix=f"component {name!r}.object_id")
            previous = seen.get(object_id)
            if previous is not None and previous != name:
                raise ComponentRepositoryError(
                    f"immutable object id {object_id!r} is assigned to both {previous!r} and {name!r}"
                )
            seen[object_id] = name
        object_ids = set(seen)
        for name, definition in self.components.items():
            owner = definition.owner_object_id
            if owner is None:
                continue
            owner = _normalize_object_id(owner, prefix=f"component {name!r}.owner_object_id")
            if owner == definition.object_id:
                raise ComponentRepositoryError(f"component {name!r} cannot own itself")
            if owner not in object_ids:
                raise ComponentRepositoryError(
                    f"component {name!r}.owner_object_id does not identify a repository object"
                )
        self._validate_ownership_cycles()

    def _validate_ownership_cycles(self) -> None:
        by_id = {item.object_id: item for item in self.components.values()}
        for definition in self.components.values():
            visited = {definition.object_id}
            owner = definition.owner_object_id
            while owner is not None:
                if owner in visited:
                    raise ComponentRepositoryError(f"component {definition.component_id!r} has cyclic ownership")
                visited.add(owner)
                owner = by_id[owner].owner_object_id

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
            legacy_override = raw.get("version", 1) in {1, 2}
            for name, definition in repository.components.items():
                existing = merged.get(name)
                if existing is not None and existing != definition:
                    if legacy_override:
                        merged[name] = definition
                        continue
                    raise ComponentRepositoryError(
                        f"repository object name {name!r} is defined more than once; use explicit repository scopes and overrides"
                    )
                for existing_name, existing_definition in merged.items():
                    if existing_name != name and existing_definition.object_id == definition.object_id:
                        raise ComponentRepositoryError(
                            f"immutable object id {definition.object_id!r} is defined by both {existing_name!r} and {name!r}"
                        )
                merged[name] = definition
        return cls(merged)

    @classmethod
    def load_recoverable(
        cls, paths: Iterable[Path],
    ) -> tuple["ComponentRepository", tuple[tuple[str, str], ...]]:
        """Load valid entries while reporting entries that block recovery.

        This is intentionally separate from :meth:`load`: normal execution
        remains strict, while the Workbench can still open a repository whose
        previous capture wrote one malformed object and offer an explicit,
        recoverable repair action.
        """
        merged: dict[str, ComponentDefinition] = {}
        issues: list[tuple[str, str]] = []
        for path in paths:
            path = Path(path)
            if not path.is_file():
                continue
            try:
                raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as exc:
                raise ComponentRepositoryError(f"invalid YAML in {path}: {exc}") from exc
            if not isinstance(raw, dict):
                raise ComponentRepositoryError(f"{path}: root must be a mapping")
            version = raw.get("version", 1)
            entries = raw.get("components", {})
            if version not in {1, 2, 3} or not isinstance(entries, dict):
                raise ComponentRepositoryError(
                    f"{path}: repository schema cannot be recovered"
                )
            for name, value in entries.items():
                name = str(name)
                try:
                    parsed = cls.from_document(
                        {"version": version, "components": {name: value}},
                        source=str(path),
                    )
                    definition = parsed.get(name)
                except Exception as exc:
                    issues.append((name, str(exc)))
                    continue
                existing = merged.get(name)
                if existing is not None and existing.object_id != definition.object_id:
                    definition = replace(definition, object_id=existing.object_id)
                merged[name] = definition

        # Per-entry parsing cannot see cross-entry ownership failures. Remove
        # only the smallest set of entries needed to construct a valid
        # repository and report each one for the explicit repair action.
        while True:
            try:
                return cls(merged).with_inferred_ownership(), tuple(issues)
            except ComponentRepositoryError as exc:
                removable = None
                for name in tuple(merged):
                    candidate = dict(merged)
                    candidate.pop(name)
                    try:
                        cls(candidate).with_inferred_ownership()
                    except ComponentRepositoryError:
                        continue
                    removable = name
                    break
                if removable is None:
                    raise ComponentRepositoryError(
                        "repository recovery could not isolate invalid entries: %s" % exc
                    ) from exc
                merged.pop(removable)
                issues.append((removable, str(exc)))

    @classmethod
    def from_document(cls, raw: Any, *, source: str = "repository") -> "ComponentRepository":
        if not isinstance(raw, dict):
            raise ComponentRepositoryError(f"{source}: root must be a mapping")
        version = raw.get("version", 1)
        if version not in {1, 2, 3}:
            raise ComponentRepositoryError(f"{source}: unsupported component schema version {version!r}")
        entries = raw.get("components", {})
        if not isinstance(entries, dict):
            raise ComponentRepositoryError(f"{source}: components must be a mapping")
        return cls({
            str(name): _parse_component(Path(source), str(name), value, version=version)
            for name, value in entries.items()
        })

    def get(self, component_id: str) -> ComponentDefinition:
        try:
            return self.components[component_id]
        except KeyError:
            pass
        for definition in self.components.values():
            if definition.object_id == component_id:
                return definition
        candidates = self.suggest(component_id)
        suffix = f"; possible matches: {', '.join(candidates)}" if candidates else ""
        raise ComponentRepositoryError(f"unknown component {component_id!r}{suffix}")

    def contains(self, component_id: str) -> bool:
        return component_id in self.components or any(
            definition.object_id == component_id for definition in self.components.values()
        )

    def object_id_for(self, component_id: str) -> str:
        return self.get(component_id).object_id

    def suggest(self, component_id: str, *, limit: int = 3) -> list[str]:
        from difflib import get_close_matches
        return get_close_matches(component_id, self.components.keys(), n=limit, cutoff=0.45)

    def to_document(self) -> dict[str, Any]:
        return {
            "version": 3,
            "components": {
                name: _component_to_mapping(definition)
                for name, definition in sorted(self.components.items())
            },
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # A repository is the source of UUID references held by plans.  Never
        # expose a partially-written YAML document to another open authoring
        # window: write beside the destination, fsync it, then atomically
        # replace the old version.
        document = yaml.safe_dump(self.to_document(), sort_keys=False, allow_unicode=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".%s." % path.name, suffix=".tmp", dir=str(path.parent), text=True,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(document)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, str(path))
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise

    def validate_persistence(self) -> "ComponentRepository":
        """Validate the repository through its serialized schema boundary."""
        return ComponentRepository.from_document(
            self.to_document(), source="repository persistence validation",
        )

    def with_component(self, definition: ComponentDefinition) -> "ComponentRepository":
        merged = dict(self.components)
        existing = merged.get(definition.component_id)
        if existing is not None and existing.object_id != definition.object_id:
            definition = replace(definition, object_id=existing.object_id)
        merged[definition.component_id] = definition
        return ComponentRepository(merged)

    def without_component(
        self,
        component_id: str,
        *,
        reparent_children: bool = False,
    ) -> "ComponentRepository":
        """Remove an object without leaving dangling immutable ownership."""
        merged = dict(self.components)
        try:
            target = self.get(component_id)
        except ComponentRepositoryError:
            return self
        children = [
            definition for definition in merged.values()
            if definition.owner_object_id == target.object_id
        ]
        if children and not reparent_children:
            raise ComponentRepositoryError(
                "cannot delete %r while %d child object(s) still reference it; "
                "use reparent_children=True" % (target.component_id, len(children))
            )
        merged.pop(target.component_id, None)
        for child in children:
            merged[child.component_id] = replace(
                child, owner_object_id=target.owner_object_id,
            )
        return ComponentRepository(merged)

    def delete_subtree(self, component_id: str) -> tuple["ComponentRepository", tuple[str, ...]]:
        """Delete an object and every object whose ownership depends on it."""
        root = self.get(component_id)
        removed_ids = {root.object_id}
        changed = True
        while changed:
            changed = False
            for definition in self.components.values():
                if definition.owner_object_id in removed_ids and definition.object_id not in removed_ids:
                    removed_ids.add(definition.object_id)
                    changed = True
        removed_names = tuple(
            name for name, definition in self.components.items()
            if definition.object_id in removed_ids
        )
        return ComponentRepository({
            name: definition for name, definition in self.components.items()
            if definition.object_id not in removed_ids
        }), removed_names

    def rename(self, component_id: str, new_component_id: str) -> "ComponentRepository":
        if not isinstance(new_component_id, str) or not new_component_id.strip():
            raise ComponentRepositoryError("new component name must be a non-empty string")
        new_component_id = new_component_id.strip()
        definition = self.get(component_id)
        if new_component_id != definition.component_id and new_component_id in self.components:
            raise ComponentRepositoryError(f"component {new_component_id!r} already exists")
        merged = dict(self.components)
        merged.pop(definition.component_id)
        merged[new_component_id] = replace(definition, component_id=new_component_id)
        return ComponentRepository(merged)

    def overlay(self, other: "ComponentRepository") -> "ComponentRepository":
        merged = dict(self.components)
        for name, definition in other.components.items():
            existing = merged.get(name)
            if existing is not None and existing != definition:
                raise ComponentRepositoryError(
                    f"repository object name {name!r} conflicts across composed repositories"
                )
            for existing_name, existing_definition in merged.items():
                if existing_name != name and existing_definition.object_id == definition.object_id:
                    raise ComponentRepositoryError(
                        f"immutable object id {definition.object_id!r} conflicts across composed repositories"
                    )
            merged[name] = definition
        return ComponentRepository(merged)


def _parse_component(path: Path, component_id: str, value: Any, *, version: int = 1) -> ComponentDefinition:
    if not isinstance(value, dict):
        raise ComponentRepositoryError(f"{path}: component {component_id!r} must be a mapping")
    raw_object_id = value.get("object_id")
    if raw_object_id is None:
        if version == 3:
            raise ComponentRepositoryError(f"{path}: component {component_id!r}.object_id is required by schema v3")
        raw_object_id = str(uuid5(NAMESPACE_URL, f"automation-harness:{component_id}"))
    object_id = _normalize_object_id(raw_object_id, prefix=f"{path}: component {component_id!r}.object_id")

    description = value.get("description", "")
    if not isinstance(description, str):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.description must be a string")
    revision = value.get("revision", 1)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.revision must be a positive integer")
    expected_states = value.get("expected_states", {})
    if not isinstance(expected_states, Mapping):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.expected_states must be a mapping")
    action_completion = _mapping_of_mappings(path, component_id, value.get("action_completion", {}), "action_completion")
    scope = value.get("scope", {})
    if not isinstance(scope, Mapping):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.scope must be a mapping")
    visual = _normalize_visual(path, component_id, value.get("visual"))
    raw_owner_object_id = value.get("owner_object_id")
    owner_object_id = None if raw_owner_object_id is None else _normalize_object_id(
        raw_owner_object_id, prefix=f"{path}: component {component_id!r}.owner_object_id"
    )

    raw_object_type = value.get("object_type")
    if raw_object_type is None:
        object_type = ObjectType.CUSTOM
    else:
        try:
            object_type = ObjectType(str(raw_object_type))
        except ValueError as exc:
            raise ComponentRepositoryError(f"{path}: component {component_id!r}.object_type is not a known semantic type") from exc
    raw_actions = value.get("actions")
    if raw_actions is None:
        raw_actions = [item.value for item in default_actions(object_type)]
        if not raw_actions and version in {1, 2}:
            raw_actions = ["resolve"]
    if not isinstance(raw_actions, list) or not raw_actions or not all(isinstance(item, str) and item for item in raw_actions):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.actions must be a non-empty list of strings")
    actions = frozenset(raw_actions)

    raw_strategies = value.get("strategies", [])
    if not isinstance(raw_strategies, list) or not raw_strategies:
        raise ComponentRepositoryError(f"{path}: component {component_id!r} requires at least one strategy")
    strategies: list[ComponentStrategy] = []
    for index, raw in enumerate(raw_strategies):
        if not isinstance(raw, dict):
            raise ComponentRepositoryError(f"{path}: component {component_id!r}.strategies[{index}] must be a mapping")
        strategy_type = raw.get("type")
        if not isinstance(strategy_type, str) or not strategy_type:
            raise ComponentRepositoryError(f"{path}: component {component_id!r}.strategies[{index}].type must be a non-empty string")
        if strategy_type == "reference":
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r} uses removed strategy 'reference'; use 'reference_inspection' only for non-interactive synthetic inspection"
            )
        if strategy_type == "reference_inspection" and "activate" in actions:
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r} cannot declare activate with reference_inspection; synthetic inspection may locate evidence but may not perform UI interaction"
            )
        if strategy_type == "anchored_visual" and "activate" in actions:
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r} cannot declare activate with anchored_visual; visual targets are externally resolved and read-only"
            )
        options = {k: v for k, v in raw.items() if k != "type"}
        if strategy_type in {"atspi", "java_accessibility"}:
            if "identification" not in options:
                options = {"identification": {"mandatory": {
                    key: value for key, value in options.items() if key in _ATSPI_SIMPLE_KEYS
                }}}
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
        action_completion=action_completion,
        scope=dict(scope),
        object_id=object_id,
        owner_object_id=owner_object_id,
    )


def _mapping_of_mappings(path: Path, component_id: str, value: Any, field_name: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise ComponentRepositoryError(f"{path}: component {component_id!r}.{field_name} must be a mapping")
    result: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or not isinstance(item, Mapping):
            raise ComponentRepositoryError(
                f"{path}: component {component_id!r}.{field_name} must map non-empty names to mappings"
            )
        result[key] = dict(item)
    return result


def _normalize_atspi_strategy(path: Path, component_id: str, index: int, options: Mapping[str, Any]) -> dict[str, Any]:
    prefix = f"{path}: component {component_id!r}.strategies[{index}]"
    if "identification" not in options:
        raise ComponentRepositoryError(f"{prefix}.identification is required")
    raw_identity = options["identification"]
    if len(options) != 1:
        extra = sorted(set(options) - {"identification"})
        raise ComponentRepositoryError(f"{prefix}: nested AT-SPI identification cannot be mixed with flat properties: {', '.join(extra)}")
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
    if ordinal is not None and (not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0):
        raise ComponentRepositoryError(f"{prefix}.identification.ordinal must be a non-negative integer")
    identity: dict[str, Any] = {"mandatory": dict(mandatory)}
    if assistive:
        identity["assistive"] = dict(assistive)
    if ordinal is not None:
        identity["ordinal"] = ordinal
    return {"identification": identity}


def _normalize_anchored_visual_strategy(path: Path, component_id: str, index: int, options: Mapping[str, Any]) -> dict[str, Any]:
    prefix = f"{path}: component {component_id!r}.strategies[{index}]"
    if set(options) != {"anchor_identification", "relative_bounds"}:
        raise ComponentRepositoryError(f"{prefix} must contain only anchor_identification and relative_bounds")
    normalized_anchor = _normalize_atspi_strategy(path, component_id, index, {"identification": options["anchor_identification"]})["identification"]
    relative = options["relative_bounds"]
    if not isinstance(relative, list) or len(relative) != 4 or any(not isinstance(v, (int, float)) or isinstance(v, bool) for v in relative):
        raise ComponentRepositoryError(f"{prefix}.relative_bounds must be four numbers")
    rx, ry, rw, rh = (float(v) for v in relative)
    if min(rx, ry, rw, rh) < 0 or rw <= 0 or rh <= 0 or rx + rw > 1.0001 or ry + rh > 1.0001:
        raise ComponentRepositoryError(f"{prefix}.relative_bounds must be positive normalized coordinates within the anchor")
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
                raise ComponentRepositoryError(f"{prefix}.parent contains unsupported properties: {', '.join(sorted(unknown))}")
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
        "object_id": definition.object_id,
        "description": definition.description,
        "revision": definition.revision,
        "actions": sorted(definition.actions),
        "strategies": [{"type": strategy.type, **dict(strategy.options)} for strategy in definition.strategies],
        "object_type": definition.object_type.value,
    }
    if definition.expected_states:
        payload["expected_states"] = dict(definition.expected_states)
    if definition.visual:
        payload["visual"] = dict(definition.visual)
    if definition.properties:
        payload["properties"] = dict(definition.properties)
    if definition.framework:
        payload["framework"] = definition.framework
    if definition.native_class:
        payload["native_class"] = definition.native_class
    if definition.subobjects:
        payload["subobjects"] = {key: dict(value) for key, value in definition.subobjects.items()}
    if definition.action_completion:
        payload["action_completion"] = {key: dict(value) for key, value in definition.action_completion.items()}
    if definition.scope:
        payload["scope"] = dict(definition.scope)
    if definition.owner_object_id:
        payload["owner_object_id"] = definition.owner_object_id
    return payload




def _normalize_object_id(value: Any, *, prefix: str) -> str:
    if not isinstance(value, str) or not value:
        raise ComponentRepositoryError(f"{prefix} must be a UUID string")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ComponentRepositoryError(f"{prefix} must be a UUID string") from exc


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
        if not isinstance(profile, Mapping) or not profile or not all(
            isinstance(k, str) and isinstance(v, str) and v for k, v in profile.items()
        ):
            raise ComponentRepositoryError(f"{prefix}.variants.{key}.profile must be a non-empty string mapping")
        item["profile"] = dict(profile)
        component_revision = raw.get("component_revision")
        if not isinstance(component_revision, int) or isinstance(component_revision, bool) or component_revision < 1:
            raise ComponentRepositoryError(f"{prefix}.variants.{key}.component_revision must be a positive integer")
        item["component_revision"] = component_revision
        for field, default in (("pixel_tolerance", 12), ("max_difference_ratio", 0.01)):
            field_value = raw.get(field, default)
            if (
                not isinstance(field_value, (int, float))
                or isinstance(field_value, bool)
                or field_value < 0
                or (field == "max_difference_ratio" and field_value > 1)
            ):
                raise ComponentRepositoryError(f"{prefix}.variants.{key}.{field} is invalid")
            item[field] = field_value
        normalized["variants"][key] = item
    return normalized


def _validate_visual_path(prefix: str, value: str) -> None:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts or not candidate.parts or candidate.parts[0] != "visual":
        raise ComponentRepositoryError(f"{prefix}: visual assets must be relative paths under visual/")
