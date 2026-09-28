from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Iterable
from uuid import uuid4

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.component_naming import unique_component_name
from automation_harness.core.captured_repository import materialize_capture
from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService
from automation_harness.core.object_hierarchy import hierarchy_contract
from automation_harness.core.repository_scope import (
    OVERRIDE_PROPERTY,
    RepositoryAssociation,
    RepositoryScope,
    RepositorySet,
)
from automation_harness.core.solipsys_identity import locator_is_complete, strategy_parts
from automation_harness.models.component import CapturedComponent, ComponentDefinition


_METADATA_KEY = "__authoring_object_repository__"


def assigned_repositories(plan, plan_path: Path) -> tuple[RepositoryAssociation, ...]:
    metadata = plan.step_definitions.get(_METADATA_KEY) if isinstance(plan.step_definitions, Mapping) else None
    if not isinstance(metadata, Mapping):
        return ()
    raw_items = metadata.get("repositories")
    if isinstance(raw_items, list):
        result = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                raise ValueError("object repository association must be a mapping")
            value = item.get("path")
            try:
                scope = RepositoryScope(str(item.get("scope")))
            except ValueError as exc:
                raise ValueError("object repository association scope must be 'local' or 'shared'") from exc
            if not isinstance(value, str) or not value.strip():
                raise ValueError("object repository association requires a path")
            result.append(RepositoryAssociation(_resolve_repository_path(value, plan_path), scope))
        return tuple(result)
    return ()


def assigned_repository_path(plan, plan_path: Path) -> Path | None:
    associations = assigned_repositories(plan, plan_path)
    local = next((item.path for item in associations if item.scope is RepositoryScope.LOCAL), None)
    return local or (associations[0].path if associations else None)


def assign_repositories(plan, plan_path: Path, associations: Iterable[RepositoryAssociation]):
    plan_path = Path(plan_path).resolve()
    items = tuple(associations)
    # Validate cardinality and ordering semantics before persisting metadata.
    RepositorySet(items, tuple(ComponentRepository({}) for _ in items))
    payload = []
    for item in items:
        path = Path(item.path).resolve()
        try:
            stored = path.relative_to(plan_path.parent).as_posix()
        except ValueError:
            stored = str(path)
        payload.append({"path": stored, "scope": item.scope.value})
    definitions = dict(plan.step_definitions)
    definitions[_METADATA_KEY] = {"kind": "object_repositories", "repositories": payload}
    return replace(plan, step_definitions=definitions)


def load_repository_set(plan, plan_path: Path) -> RepositorySet:
    return RepositorySet.load(assigned_repositories(plan, plan_path))


def ensure_default_repository(plan, plan_path: Path):
    associations = assigned_repositories(plan, plan_path)
    existing = next((item.path for item in associations if item.scope is RepositoryScope.LOCAL), None)
    if existing is not None:
        if not existing.exists():
            ComponentRepository({}).save(existing)
        return plan, existing
    default_path = Path(plan_path).resolve().with_suffix(".ahobjects")
    if not default_path.exists():
        ComponentRepository({}).save(default_path)
    if associations:
        plan = assign_repositories(
            plan, plan_path,
            (RepositoryAssociation(default_path, RepositoryScope.LOCAL), *associations),
        )
    else:
        plan = assign_repositories(plan, plan_path, (
            RepositoryAssociation(default_path, RepositoryScope.LOCAL),
        ))
    return plan, default_path


def load_authoring_repository(plan, plan_path: Path) -> tuple[ComponentRepository, Path | None]:
    associations = assigned_repositories(plan, plan_path)
    if not associations:
        return ComponentRepository({}), None
    path = next((item.path for item in associations if item.scope is RepositoryScope.LOCAL), None)
    # This API is used by editors which save the returned repository back to
    # the returned path. Never return a composed view here: doing so would copy
    # shared objects into the local file. Read-only/execution consumers use
    # load_repository_set(...).compose().
    path = path or associations[0].path
    return ComponentRepository.load((path,)), path


def _resolve_repository_path(value: str, plan_path: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path(plan_path).resolve().parent / candidate
    return candidate.resolve()


def materialize_captured_target(repository: ComponentRepository, capture: CapturedComponent) -> tuple[ComponentRepository, str, bool]:
    matches = matching_component_ids(repository, capture)
    if len(matches) == 1:
        return repository, matches[0], False
    if len(matches) > 1:
        raise ValueError("captured target matches multiple repository objects: %s" % ", ".join(matches))
    component_id = _unique_component_id(repository, capture)
    if _is_runtime_only_solipsys_capture(capture):
        definition = _runtime_only_solipsys_definition(component_id, capture)
        return repository.with_component(definition), definition.object_id, True
    repository, definition, _created = materialize_capture(
        HybridObjectCaptureService(), repository, component_id, capture,
        validate_live=False,
    )
    return repository, definition.object_id, True


def persist_recorded_menu_owner(
    local_repository: ComponentRepository,
    effective_repository: ComponentRepository,
    owner_object_id: str,
) -> ComponentRepository:
    """Persist recorded menu routes locally without mutating a shared repository."""
    source = effective_repository.get(owner_object_id)
    if local_repository.contains(owner_object_id):
        existing = local_repository.get(owner_object_id)
        return local_repository.with_component(replace(
            source,
            component_id=existing.component_id,
            object_id=existing.object_id,
            properties=dict(existing.properties),
        ))
    for existing in local_repository.components.values():
        if existing.properties.get(OVERRIDE_PROPERTY) == owner_object_id:
            return local_repository.with_component(replace(
                source,
                component_id=existing.component_id,
                object_id=existing.object_id,
                properties=dict(existing.properties),
            ))

    # A newly discovered ContextMenu belongs to the local repository directly.
    if source.properties.get("logical_owner") == "context_menu":
        return local_repository.with_component(source)

    properties = dict(source.properties)
    properties[OVERRIDE_PROPERTY] = owner_object_id
    local_name = source.component_id
    suffix = 2
    while local_name in local_repository.components:
        local_name = "%s-%d" % (source.component_id, suffix)
        suffix += 1
    return local_repository.with_component(replace(
        source,
        component_id=local_name,
        object_id=str(uuid4()),
        properties=properties,
    ))


def _is_runtime_only_solipsys_capture(capture: CapturedComponent) -> bool:
    if capture.framework != "solipsys_rendered":
        return False
    strategy = capture.candidate_strategy()
    if strategy.type != "java_agent":
        return False
    mandatory, _assistive = strategy_parts(strategy.options)
    return not locator_is_complete(mandatory)


def _runtime_only_solipsys_definition(
    component_id: str, capture: CapturedComponent,
) -> ComponentDefinition:
    """Retain a discovered live track without claiming durable resolution.

    Runtime correlation is useful for review, inspection, and de-duplication in
    the current MSCT process.  The durable capture service continues to reject
    this strategy if a user later attempts to promote it as executable identity.
    """
    properties = dict(capture.backend_properties or {})
    properties.update({
        "identity_state": "runtime_only",
        "locator_status": "provisional",
        "cross_session_resolution": False,
        "persistence_warning": (
            "Captured track has no validated durable identity; its runtime "
            "reference is valid only for the current MSCT process."
        ),
    })
    actions = frozenset(capture.actions or ("resolve", "click"))
    return ComponentDefinition(
        component_id=component_id,
        description=capture.description or capture.name or "Captured MSCT track (runtime only)",
        strategies=(capture.candidate_strategy(),),
        actions=actions,
        object_type=capture.semantic_type(),
        properties=properties,
        framework=capture.framework,
        native_class=capture.native_class,
        scope=hierarchy_contract(capture),
        subobjects={str(key): dict(value) for key, value in capture.logical_subobjects.items()},
    )


def matching_component_ids(repository: ComponentRepository, capture: CapturedComponent) -> list[str]:
    result = []
    candidate = capture.candidate_strategy()
    for component_id, definition in repository.components.items():
        if definition.object_type != capture.semantic_type() and definition.object_type.value != "custom":
            continue
        if candidate in definition.strategies:
            result.append(definition.object_id)
            continue
        for strategy in definition.strategies:
            if strategy.type == candidate.type and strategy.options == candidate.options:
                result.append(definition.object_id)
                break
    return result


def definition_from_capture(component_id: str, capture: CapturedComponent) -> ComponentDefinition:
    actions = frozenset(capture.actions or ("resolve", "activate"))
    if "resolve" not in actions:
        actions = frozenset((*actions, "resolve"))
    return ComponentDefinition(
        component_id=component_id,
        description=capture.description or capture.name or "Recorded object",
        strategies=(capture.candidate_strategy(),),
        actions=actions,
        object_type=capture.semantic_type(),
        properties=dict(capture.backend_properties),
        framework=None,
        native_class=None,
        subobjects={str(key): dict(value) for key, value in capture.logical_subobjects.items()},
    )


def merge_repository_or(target: ComponentRepository, source: ComponentRepository) -> tuple[ComponentRepository, dict[str, int]]:
    """Merge one logical Object Repository into another using OR locators.

    The target repository is canonical. Immutable IDs and component names that
    already exist in the target remain canonical; compatible source definitions
    contribute resolver alternatives and metadata. Objects absent from the
    target are copied as-is (normalized to framework-agnostic logical objects).
    """
    result = target
    stats = {"added": 0, "merged": 0, "unchanged": 0}

    for source_name, source_definition in sorted(source.components.items()):
        canonical_name = _canonical_target_name(result, source_definition)
        normalized_source = replace(source_definition, framework=None, native_class=None)
        if canonical_name is None:
            result = result.with_component(normalized_source)
            stats["added"] += 1
            continue

        current = result.get(canonical_name)
        merged = _merge_definition_or(current, normalized_source)
        if merged == current:
            stats["unchanged"] += 1
            continue
        result = result.with_component(merged)
        stats["merged"] += 1

    return result, stats


def merge_objects_or(repository: ComponentRepository, target_id: str, source_ids: Iterable[str]) -> ComponentRepository:
    target = repository.get(target_id)
    sources = [repository.get(value) for value in source_ids if value != target_id]
    if not sources:
        return repository
    merged = target
    for source in sources:
        merged = _merge_definition_or(merged, source)
    result = repository.with_component(merged)
    for source in sources:
        result = result.without_component(source.component_id)
    return result


def _canonical_target_name(repository: ComponentRepository, source: ComponentDefinition) -> str | None:
    for name, definition in repository.components.items():
        if definition.object_id == source.object_id:
            return name
    if source.component_id in repository.components:
        return source.component_id
    return None


def _merge_definition_or(target: ComponentDefinition, source: ComponentDefinition) -> ComponentDefinition:
    if target.object_type != source.object_type:
        if target.object_type.value == "custom":
            object_type = source.object_type
        elif source.object_type.value == "custom":
            object_type = target.object_type
        else:
            raise ValueError(
                "OR merge requires compatible semantic object types: %s is %s, %s is %s"
                % (target.component_id, target.object_type.value, source.component_id, source.object_type.value)
            )
    else:
        object_type = target.object_type

    strategies = list(target.strategies)
    for strategy in source.strategies:
        if strategy not in strategies:
            strategies.append(strategy)

    actions = frozenset(set(target.actions) | set(source.actions))
    properties = dict(target.properties)
    properties.update({key: value for key, value in source.properties.items() if key not in properties})
    subobjects = {str(key): dict(value) for key, value in target.subobjects.items()}
    subobjects.update({str(key): dict(value) for key, value in source.subobjects.items() if key not in subobjects})
    expected_states = dict(target.expected_states)
    expected_states.update({key: value for key, value in source.expected_states.items() if key not in expected_states})
    action_completion = {str(key): dict(value) for key, value in target.action_completion.items()}
    action_completion.update({str(key): dict(value) for key, value in source.action_completion.items() if key not in action_completion})
    scope = dict(target.scope)
    scope.update({key: value for key, value in source.scope.items() if key not in scope})

    descriptions = []
    for value in (target.description, source.description):
        if value and value not in descriptions:
            descriptions.append(value)

    changed = (
        tuple(strategies) != tuple(target.strategies)
        or actions != target.actions
        or properties != dict(target.properties)
        or subobjects != {str(key): dict(value) for key, value in target.subobjects.items()}
        or expected_states != dict(target.expected_states)
        or action_completion != {str(key): dict(value) for key, value in target.action_completion.items()}
        or scope != dict(target.scope)
        or object_type != target.object_type
        or target.framework is not None
        or target.native_class is not None
        or (not target.visual and source.visual)
    )
    if not changed:
        return target

    return replace(
        target,
        strategies=tuple(strategies),
        actions=actions,
        properties=properties,
        subobjects=subobjects,
        expected_states=expected_states,
        action_completion=action_completion,
        scope=scope,
        object_type=object_type,
        description=" / ".join(descriptions),
        visual=target.visual or source.visual,
        framework=None,
        native_class=None,
        revision=max(target.revision, source.revision) + 1,
    )


def _unique_component_id(repository: ComponentRepository, capture: CapturedComponent) -> str:
    return unique_component_name(repository.components, capture)
