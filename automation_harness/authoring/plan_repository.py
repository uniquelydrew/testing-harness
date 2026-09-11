from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Iterable

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import CapturedComponent, ComponentDefinition


_METADATA_KEY = "__authoring_object_repository__"


def assigned_repository_path(plan, plan_path: Path) -> Path | None:
    metadata = plan.step_definitions.get(_METADATA_KEY) if isinstance(plan.step_definitions, Mapping) else None
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("path")
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = Path(plan_path).resolve().parent / candidate
    return candidate.resolve()


def assign_repository(plan, plan_path: Path, repository_path: Path):
    plan_path = Path(plan_path).resolve()
    repository_path = Path(repository_path).resolve()
    try:
        stored = repository_path.relative_to(plan_path.parent).as_posix()
    except ValueError:
        stored = str(repository_path)
    definitions = dict(plan.step_definitions)
    definitions[_METADATA_KEY] = {"kind": "object_repository", "path": stored}
    return replace(plan, step_definitions=definitions)


def ensure_default_repository(plan, plan_path: Path):
    existing = assigned_repository_path(plan, plan_path)
    if existing is not None:
        if not existing.exists():
            ComponentRepository({}).save(existing)
        return plan, existing
    default_path = Path(plan_path).resolve().with_suffix(".ahobjects")
    if not default_path.exists():
        ComponentRepository({}).save(default_path)
    return assign_repository(plan, plan_path, default_path), default_path


def load_authoring_repository(plan, plan_path: Path) -> tuple[ComponentRepository, Path | None]:
    path = assigned_repository_path(plan, plan_path)
    if path is None:
        return ComponentRepository({}), None
    return ComponentRepository.load((path,)), path


def materialize_captured_target(repository: ComponentRepository, capture: CapturedComponent) -> tuple[ComponentRepository, str, bool]:
    matches = matching_component_ids(repository, capture)
    if len(matches) == 1:
        return repository, matches[0], False
    if len(matches) > 1:
        raise ValueError("captured target matches multiple repository objects: %s" % ", ".join(matches))
    component_id = _unique_component_id(repository, capture)
    definition = definition_from_capture(component_id, capture)
    return repository.with_component(definition), component_id, True


def matching_component_ids(repository: ComponentRepository, capture: CapturedComponent) -> list[str]:
    result = []
    candidate = capture.candidate_strategy()
    for component_id, definition in repository.components.items():
        if definition.object_type != capture.semantic_type() and definition.object_type.value != "custom":
            continue
        if candidate in definition.strategies:
            result.append(component_id)
            continue
        for strategy in definition.strategies:
            if strategy.type == candidate.type and strategy.options == candidate.options:
                result.append(component_id)
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
    raw = _qualified_capture_name(capture)
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw.strip()).strip("-.") or "RecordedObject"
    candidate = base
    index = 2
    while candidate in repository.components:
        candidate = "%s-%d" % (base, index)
        index += 1
    return candidate


def _qualified_capture_name(capture: CapturedComponent) -> str:
    raw = list(getattr(capture, "hierarchy", ()) or ())
    if not raw:
        raw = [capture.window or capture.application, capture.accessible_id or capture.name or capture.role]
    segments = []
    for value in raw:
        if value in (None, ""):
            continue
        text = _semantic_segment(value)
        if text and (not segments or text != segments[-1]):
            segments.append(text)
    return ".".join(segments) or "RecordedObject"


def _semantic_segment(value) -> str:
    output = []
    capitalize = True
    for character in str(value or "").strip():
        if character.isalnum():
            output.append(character.upper() if capitalize else character)
            capitalize = False
        else:
            capitalize = True
    return "".join(output) or "Object"
