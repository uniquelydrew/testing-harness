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
        if definition.framework and capture.framework and definition.framework != capture.framework:
            continue
        if definition.object_type != capture.semantic_type() and definition.object_type.value != "custom":
            continue
        if candidate in definition.strategies:
            result.append(component_id)
            continue
        for strategy in definition.strategies:
            if strategy.type == candidate.type and strategy.options == candidate.options:
                result.append(component_id); break
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
        framework=capture.framework,
        native_class=capture.native_class,
        subobjects={str(key): dict(value) for key, value in capture.logical_subobjects.items()},
    )


def merge_objects_or(repository: ComponentRepository, target_id: str, source_ids: Iterable[str]) -> ComponentRepository:
    target = repository.get(target_id)
    sources = [repository.get(value) for value in source_ids if value != target_id]
    if not sources:
        return repository
    strategies = list(target.strategies)
    actions = set(target.actions)
    properties = dict(target.properties)
    subobjects = {str(k): dict(v) for k, v in target.subobjects.items()}
    descriptions = [target.description] if target.description else []
    for source in sources:
        if source.object_type != target.object_type:
            raise ValueError("OR merge requires the same semantic object type")
        for strategy in source.strategies:
            if strategy not in strategies:
                strategies.append(strategy)
        actions.update(source.actions)
        properties.update({k: v for k, v in source.properties.items() if k not in properties})
        subobjects.update({k: dict(v) for k, v in source.subobjects.items() if k not in subobjects})
        if source.description and source.description not in descriptions:
            descriptions.append(source.description)
    merged = replace(
        target,
        strategies=tuple(strategies),
        actions=frozenset(actions),
        properties=properties,
        subobjects=subobjects,
        description=" / ".join(descriptions),
        revision=max([target.revision, *[item.revision for item in sources]]) + 1,
    )
    result = repository.with_component(merged)
    for source in sources:
        result = result.without_component(source.component_id)
    return result


def _unique_component_id(repository: ComponentRepository, capture: CapturedComponent) -> str:
    raw = capture.accessible_id or capture.name or capture.role or "recorded-object"
    base = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw.strip()).strip("-.").lower() or "recorded-object"
    candidate = base
    index = 2
    while candidate in repository.components:
        candidate = "%s-%d" % (base, index); index += 1
    return candidate
