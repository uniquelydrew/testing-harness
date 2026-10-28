"""One canonical contract for object identity, snapshots, and execution inputs."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.reusable_step_expansion import expand_reusable_steps
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import repository_from_plan, validate_plan, validate_plan_components, validate_plan_execution
from automation_harness.models.component import CapturedComponent


SNAPSHOT_METADATA_KEY = "__repository_snapshot__"


class ExecutionMode(str, Enum):
    LINKED = "linked"
    PORTABLE = "portable"
    LOCKED = "locked"
    CANONICAL = "canonical"


@dataclass(frozen=True)
class IdentityAssessment:
    """The sole capture-equivalence decision returned to authoring callers."""
    matches: tuple[str, ...]
    status: str

    @classmethod
    def assess(cls, repository: ComponentRepository, captured: CapturedComponent) -> "IdentityAssessment":
        # Import here so legacy callers can retain the public helper while all
        # decisions share one implementation.
        from automation_harness.core.object_identity_sync import _strategy_matches_capture, _capture_values, _strategy_identity

        candidate = captured.candidate_strategy()
        values = _capture_values(captured, _strategy_identity(candidate.options))
        matches = []
        for definition in repository.components.values():
            if definition.framework and captured.framework and definition.framework != captured.framework:
                continue
            if (definition.object_type.value != "custom" and captured.semantic_type().value != "custom"
                    and definition.object_type != captured.semantic_type()):
                continue
            # Parent ownership is identity evidence whenever capture supplies it.
            parent_id = getattr(captured, "parent_accessible_id", None)
            if parent_id and definition.owner_object_id is None:
                continue
            if parent_id and definition.owner_object_id is not None:
                owner = repository.get(definition.owner_object_id)
                owner_values = _strategy_identity(owner.strategies[0].options) if owner.strategies else {}
                if owner_values.get("accessible_id") not in {parent_id, None} and owner_values.get("id") not in {parent_id, None}:
                    continue
            if candidate in definition.strategies or any(_strategy_matches_capture(strategy, candidate, values) for strategy in definition.strategies):
                matches.append(definition.object_id)
        status = "known_unique" if len(matches) == 1 else "ambiguous" if matches else "new_candidate"
        return cls(tuple(matches), status)


@dataclass(frozen=True)
class PreparedRepository:
    repository: ComponentRepository
    plan: Any
    mode: ExecutionMode
    provenance: Mapping[str, Mapping[str, Any]]
    fingerprint: str
    issues: tuple[str, ...]


def definition_fingerprint(definition: Any) -> str:
    """Stable full-definition fingerprint excluding mutable labels and source paths."""
    payload = {
        "object_id": definition.object_id, "description": definition.description,
        "revision": definition.revision, "object_type": definition.object_type.value,
        "strategies": [{"type": item.type, **dict(item.options)} for item in definition.strategies],
        "actions": sorted(definition.actions), "expected_states": dict(definition.expected_states),
        "visual": dict(definition.visual or {}), "properties": dict(definition.properties),
        "framework": definition.framework, "native_class": definition.native_class,
        "owner_object_id": definition.owner_object_id, "scope": dict(definition.scope),
        "subobjects": dict(definition.subobjects), "action_completion": dict(definition.action_completion),
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


class RepositoryResolver:
    """Composes portable and canonical definitions without name-based identity."""

    def resolve(self, plan, *, canonical_repository=None, mode=None) -> PreparedRepository:
        selected = ExecutionMode(mode or getattr(plan, "execution_mode", None) or ExecutionMode.LINKED.value)
        snapshot = self._snapshot_with_owners(plan, canonical_repository)
        metadata = _snapshot_metadata(plan)
        if selected is ExecutionMode.CANONICAL:
            if canonical_repository is None:
                raise ComponentRepositoryError("canonical execution requires an assigned external object repository")
            effective = canonical_repository
        elif selected is ExecutionMode.PORTABLE or canonical_repository is None:
            effective = snapshot
        else:
            effective = self._linked(snapshot, canonical_repository)
        if selected is ExecutionMode.LOCKED:
            self._require_locked(metadata, effective)
        provenance = {
            definition.object_id: {
                "canonical_origin": metadata.get(definition.object_id, {}).get("canonical_origin", definition.object_id),
                "snapshot_revision": metadata.get(definition.object_id, {}).get("revision", definition.revision),
                "snapshot_fingerprint": metadata.get(definition.object_id, {}).get("fingerprint", definition_fingerprint(definition)),
                "resolved_revision": definition.revision,
                "resolved_fingerprint": definition_fingerprint(definition),
            }
            for definition in effective.components.values()
        }
        frozen = freeze_repository(effective)
        fingerprint = hashlib.sha256(_canonical_json(frozen.to_document()).encode("utf-8")).hexdigest()
        return PreparedRepository(frozen, plan, selected, provenance, fingerprint, ())

    @staticmethod
    def _snapshot_with_owners(plan, canonical):
        raw = dict(plan.objects)
        if canonical is not None:
            canonical_by_id = {item.object_id: item for item in canonical.components.values()}
            pending = list(raw.values())
            while pending:
                value = pending.pop()
                if not isinstance(value, Mapping):
                    continue
                owner_id = value.get("owner_object_id")
                if not owner_id or any(isinstance(item, Mapping) and item.get("object_id") == owner_id for item in raw.values()):
                    continue
                owner = canonical_by_id.get(owner_id)
                if owner is not None:
                    raw[owner.component_id] = canonical.to_document()["components"][owner.component_id]
                    pending.append(raw[owner.component_id])
        return ComponentRepository.from_document({"version": 3, "components": raw}, source="plan snapshot")

    @staticmethod
    def _linked(snapshot, canonical):
        by_id = {item.object_id: item for item in canonical.components.values()}
        components = dict(canonical.components)
        for embedded in snapshot.components.values():
            current = by_id.get(embedded.object_id)
            if current is not None:
                # Canonical definition controls compatible linked execution.
                continue
            if embedded.component_id in components:
                raise ComponentRepositoryError("embedded component %r has a different canonical object_id" % embedded.component_id)
            components[embedded.component_id] = embedded
        return ComponentRepository(components)

    @staticmethod
    def _require_locked(metadata, repository):
        missing = []
        if not metadata:
            raise ComponentRepositoryError("locked repository resolution failed: plan has no snapshot provenance")
        for object_id, entry in metadata.items():
            if not repository.contains(object_id):
                missing.append("snapshot object %s is unavailable" % object_id)
                continue
            definition = repository.get(object_id)
            if entry.get("revision") != definition.revision or entry.get("fingerprint") != definition_fingerprint(definition):
                missing.append("%s revision/fingerprint differs from locked snapshot" % definition.component_id)
        if missing:
            raise ComponentRepositoryError("locked repository resolution failed: " + "; ".join(missing))


class RepositoryQualifier:
    def qualify(self, plan, *, canonical_repository=None, reusable_steps=None, registry=None,
                backend_capabilities=None, allowed_step_risks=None, mode=None):
        registry = registry or default_step_registry()
        prepared = RepositoryResolver().resolve(plan, canonical_repository=canonical_repository, mode=mode)
        expanded = expand_reusable_steps(plan, reusable_steps) if reusable_steps else plan
        expanded = translate_legacy_component_references(expanded, prepared.repository)
        issues = validate_plan(expanded, registry)
        issues.extend(validate_plan_components(expanded, prepared.repository))
        if backend_capabilities is not None and allowed_step_risks is not None:
            issues.extend(validate_plan_execution(expanded, registry, backend_capabilities=backend_capabilities,
                                                 allowed_step_risks=allowed_step_risks))
        return PreparedRepository(prepared.repository, expanded, prepared.mode, prepared.provenance,
                                  prepared.fingerprint, tuple(issues))


def compose_recording_workspace(repository: ComponentRepository, additions: tuple[ComponentRepository, ...]) -> ComponentRepository:
    """Compose a non-executable capture workspace without changing run resolution.

    Recording repositories remain provisional by design; this explicit helper
    keeps that composition separate from the resolver used by execution.
    """
    result = repository
    for addition in additions:
        result = result.overlay(addition)
    return result


def compose_canonical_repositories(repositories: tuple[ComponentRepository, ...]) -> ComponentRepository:
    """Strictly combine explicit canonical sources before resolver selection."""
    result = ComponentRepository({})
    for repository in repositories:
        result = result.overlay(repository)
    return result


def snapshot_provenance(repository: ComponentRepository) -> dict[str, dict[str, Any]]:
    return {item.object_id: {"canonical_origin": item.object_id, "revision": item.revision,
                             "fingerprint": definition_fingerprint(item)}
            for item in repository.components.values()}


def _snapshot_metadata(plan) -> Mapping[str, Mapping[str, Any]]:
    raw = getattr(plan, "step_definitions", {}).get(SNAPSHOT_METADATA_KEY, {})
    return raw.get("objects", {}) if isinstance(raw, Mapping) and isinstance(raw.get("objects", {}), Mapping) else {}


_REFERENCE_KEYS = frozenset({"component_id", "component", "object", "window", "owner_object_id"})


def translate_legacy_component_references(plan, repository: ComponentRepository):
    """Rewrite unambiguous display-name references to canonical UUIDs."""
    names = {definition.component_id: definition.object_id for definition in repository.components.values()}
    for name, raw in getattr(plan, "objects", {}).items():
        if isinstance(raw, Mapping) and isinstance(raw.get("object_id"), str):
            names.setdefault(str(name), raw["object_id"])

    def translate(value, key=None):
        if key in _REFERENCE_KEYS and isinstance(value, str):
            return names.get(value, value)
        if isinstance(value, Mapping):
            return {item_key: translate(item_value, item_key) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [translate(item) for item in value]
        if isinstance(value, tuple):
            return tuple(translate(item) for item in value)
        return value

    return replace(plan, steps=tuple(
        replace(call, inputs=translate(call.inputs), completion=translate(call.completion), scope=translate(call.scope))
        for call in plan.steps
    ))


def freeze_repository(repository: ComponentRepository) -> ComponentRepository:
    """Create an execution-only deep read-only view of a qualified repository."""
    def freeze(value):
        if isinstance(value, Mapping):
            return MappingProxyType({key: freeze(item) for key, item in value.items()})
        if isinstance(value, list):
            return tuple(freeze(item) for item in value)
        if isinstance(value, tuple):
            return tuple(freeze(item) for item in value)
        if isinstance(value, set):
            return frozenset(freeze(item) for item in value)
        return value

    components = {}
    for definition in repository.components.values():
        strategies = tuple(replace(strategy, options=freeze(strategy.options)) for strategy in definition.strategies)
        components[definition.component_id] = replace(
            definition, strategies=strategies, expected_states=freeze(definition.expected_states),
            visual=freeze(definition.visual) if definition.visual else None, properties=freeze(definition.properties),
            subobjects=freeze(definition.subobjects), action_completion=freeze(definition.action_completion),
            scope=freeze(definition.scope),
        )
    frozen = ComponentRepository(components)
    object.__setattr__(frozen, "components", MappingProxyType(dict(frozen.components)))
    return frozen


def _canonical_json(value: Any) -> str:
    def normalize(item):
        if isinstance(item, Mapping):
            return {str(key): normalize(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(value) for value in item]
        if isinstance(item, (set, frozenset)):
            return sorted(normalize(value) for value in item)
        return item
    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":"), default=str)
