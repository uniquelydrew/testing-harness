"""Persistence for user-authored reusable step registries."""
from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

import yaml

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.formats import REPOSITORY_SUFFIX, STEP_REGISTRY_SUFFIX, with_artifact_suffix


class StepRegistryArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class AuthoringStepRegistry:
    name: str
    root: Path
    repository: Path
    steps: tuple[ReusableStepDefinition, ...] = ()

    @classmethod
    def load(cls, path: Path) -> "AuthoringStepRegistry":
        path = path.resolve()
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, Mapping):
            raise StepRegistryArtifactError("step registry root must be a mapping")
        if raw.get("version") != 1:
            raise StepRegistryArtifactError("unsupported step registry version %r" % raw.get("version"))

        name = raw.get("name")
        if not isinstance(name, str) or not name.strip():
            raise StepRegistryArtifactError("step registry requires a non-empty name")

        repository_value = raw.get("object_repository")
        if not isinstance(repository_value, str) or not repository_value.strip():
            raise StepRegistryArtifactError("step registry requires exactly one object_repository")
        repository = (path.parent / repository_value).resolve()
        if not repository.is_file():
            raise StepRegistryArtifactError("step registry object repository does not exist: %s" % repository)
        ComponentRepository.load((repository,))

        raw_steps = raw.get("steps", [])
        if not isinstance(raw_steps, list):
            raise StepRegistryArtifactError("step registry steps must be a list")
        steps = tuple(_step_from_document(item, source=path) for item in raw_steps)
        ids = [step.step_id for step in steps]
        duplicates = sorted({step_id for step_id in ids if ids.count(step_id) > 1})
        if duplicates:
            raise StepRegistryArtifactError("duplicate reusable step id(s): %s" % ", ".join(duplicates))

        return cls(name=name.strip(), root=path.parent, repository=repository, steps=steps)

    def to_document(self) -> dict[str, Any]:
        return {
            "version": 1,
            "name": self.name,
            "object_repository": os.path.relpath(str(self.repository), str(self.root)),
            "steps": [_step_to_document(step) for step in self.steps],
        }

    def with_step(self, definition: ReusableStepDefinition) -> "AuthoringStepRegistry":
        merged = [step for step in self.steps if step.step_id != definition.step_id]
        merged.append(definition)
        return replace(self, steps=tuple(sorted(merged, key=lambda item: item.step_id)))

    def without_step(self, step_id: str) -> "AuthoringStepRegistry":
        return replace(self, steps=tuple(step for step in self.steps if step.step_id != step_id))

    def get(self, step_id: str) -> ReusableStepDefinition:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        raise StepRegistryArtifactError("unknown reusable step %r" % step_id)


def create_step_registry(
    path: Path,
    name: str,
    *,
    repository: Path | None = None,
) -> AuthoringStepRegistry:
    path = with_artifact_suffix(path.resolve(), STEP_REGISTRY_SUFFIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not isinstance(name, str) or not name.strip():
        raise StepRegistryArtifactError("step registry requires a non-empty name")

    if repository is None:
        repository = path.with_name(path.name[: -len(STEP_REGISTRY_SUFFIX)] + REPOSITORY_SUFFIX)
        if not repository.exists():
            ComponentRepository({}).save(repository)
    else:
        repository = repository.resolve()
        if not repository.is_file():
            raise StepRegistryArtifactError("step registry object repository does not exist: %s" % repository)
        ComponentRepository.load((repository,))

    registry = AuthoringStepRegistry(
        name=name.strip(),
        root=path.parent,
        repository=repository.resolve(),
    )
    save_step_registry(path, registry)
    return registry


def save_step_registry(path: Path, registry: AuthoringStepRegistry) -> Path:
    path = with_artifact_suffix(path.resolve(), STEP_REGISTRY_SUFFIX)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(registry.to_document(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def _step_to_document(step: ReusableStepDefinition) -> dict[str, Any]:
    from automation_harness.core.test_plan import save_plan
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile("w+", suffix=".yaml", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        save_plan(step.plan, temporary)
        plan_document = yaml.safe_load(temporary.read_text(encoding="utf-8")) or {}
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass
    return {
        "id": step.step_id,
        "name": step.name,
        "description": step.description,
        "inputs": dict(step.inputs),
        "outputs": dict(step.outputs),
        "plan": plan_document,
    }


def _step_from_document(raw: Any, *, source: Path) -> ReusableStepDefinition:
    if not isinstance(raw, Mapping):
        raise StepRegistryArtifactError("%s: reusable step must be a mapping" % source)
    step_id = raw.get("id")
    name = raw.get("name")
    if not isinstance(step_id, str) or not step_id.strip():
        raise StepRegistryArtifactError("%s: reusable step requires a non-empty id" % source)
    if not isinstance(name, str) or not name.strip():
        raise StepRegistryArtifactError("%s: reusable step requires a non-empty name" % source)

    plan_raw = raw.get("plan")
    if not isinstance(plan_raw, Mapping):
        raise StepRegistryArtifactError("%s: reusable step %s requires a plan mapping" % (source, step_id))

    from automation_harness.core.test_plan import load_plan
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile("w+", suffix=".yaml", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(yaml.safe_dump(dict(plan_raw), sort_keys=False))
    try:
        plan = load_plan(temporary)
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass

    inputs = raw.get("inputs", {})
    outputs = raw.get("outputs", {})
    if not isinstance(inputs, Mapping) or not isinstance(outputs, Mapping):
        raise StepRegistryArtifactError("%s: reusable step %s inputs/outputs must be mappings" % (source, step_id))
    return ReusableStepDefinition(
        step_id=step_id.strip(),
        name=name.strip(),
        description=str(raw.get("description", "")),
        plan=plan,
        inputs=dict(inputs),
        outputs=dict(outputs),
    )
