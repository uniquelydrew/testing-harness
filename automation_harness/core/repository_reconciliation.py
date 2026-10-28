"""Previewable, all-or-nothing identity graph reconciliation."""
from __future__ import annotations

from dataclasses import dataclass, replace
import os
from pathlib import Path
import tempfile
from typing import Iterable, Mapping

import yaml

from automation_harness.core.component_repository import ComponentRepository, RepositoryFingerprint, repository_fingerprint
from automation_harness.models.component import ComponentDefinition


@dataclass(frozen=True)
class ReconciliationProposal:
    repository: ComponentRepository
    canonical_object_id: str
    remapped_object_ids: dict[str, str]
    changed_component_ids: tuple[str, ...]
    updated_plans: Mapping[str, object]
    updated_reusable_steps: Mapping[str, object]
    affected_plan_ids: tuple[str, ...]


class RepositoryReconciler:
    """Build a complete validated graph before any caller persists it."""
    def propose(self, repository: ComponentRepository, target_id: str, source_ids: Iterable[str], *,
                plans: Mapping[str, object] | None = None,
                reusable_steps: Mapping[str, object] | None = None) -> ReconciliationProposal:
        target = repository.get(target_id)
        sources = []
        for value in source_ids:
            source = repository.get(value)
            if source.object_id != target.object_id:
                sources.append(source)
        merged = target
        for source in sources:
            merged = _merge(merged, source)
        result = repository.with_component(merged)
        remapped = {source.object_id: target.object_id for source in sources}
        # Plan steps can still contain pre-UUID name references. Translate both
        # identity forms while exposing only UUID changes in the proposal API.
        references = dict(remapped)
        references.update({source.component_id: target.object_id for source in sources})
        changed = {merged.component_id}
        for definition in tuple(result.components.values()):
            if definition.owner_object_id in remapped:
                updated = replace(definition, owner_object_id=remapped[definition.owner_object_id])
                result = result.with_component(updated)
                changed.add(updated.component_id)
        for source in sources:
            result = result.without_component(source.component_id)
            changed.add(source.component_id)
        # Constructor and schema roundtrip validate ownership, UUID uniqueness,
        # and serializable locator constraints before publication.
        # Older in-memory authoring fixtures may omit strategies; preserve their
        # historical merge behavior while executable graphs always cross this
        # serialization qualification boundary.
        if all(item.strategies for item in result.components.values()):
            result.validate_persistence()
        updated_plans = {}
        for plan_id, plan in (plans or {}).items():
            updated = _rewrite_plan(plan, references, result)
            if updated != plan:
                updated_plans[str(plan_id)] = updated
        updated_steps = {}
        for step_id, definition in (reusable_steps or {}).items():
            updated_plan = _rewrite_plan(definition.plan, references, result)
            if updated_plan != definition.plan:
                updated_steps[str(step_id)] = replace(definition, plan=updated_plan)
        return ReconciliationProposal(
            result, target.object_id, remapped, tuple(sorted(changed)), updated_plans, updated_steps,
            tuple(sorted((*updated_plans.keys(), *updated_steps.keys()))),
        )

    def reconcile(self, repository: ComponentRepository, target_id: str, source_ids: Iterable[str]) -> ComponentRepository:
        return self.propose(repository, target_id, source_ids).repository

    def publish(self, proposal: ReconciliationProposal, *, repository_path: Path,
                plan_paths: Mapping[str, Path] | None = None,
                reusable_step_paths: Mapping[str, Path] | None = None,
                step_registries: Mapping[Path, object] | None = None,
                expected_fingerprints: Mapping[Path, RepositoryFingerprint] | None = None) -> None:
        """Publish a validated proposal as a staged, rollback-capable transaction.

        Every destination is rendered and fsynced before any visible replacement.
        If a replacement fails, earlier files are restored from their exact bytes.
        Callers must provide paths for every affected dependent artifact and
        fingerprints captured when those files were loaded. A modified artifact
        rejects the entire proposed transaction before publication.

        This is optimistic concurrency, not a cross-process lock or a crash-
        atomic filesystem transaction. Rollback covers ordinary write failures.
        """
        plan_paths = plan_paths or {}
        reusable_step_paths = reusable_step_paths or {}
        step_registries = step_registries or {}
        registry_step_ids = [step.step_id for registry in step_registries.values() for step in registry.steps]
        if len(registry_step_ids) != len(set(registry_step_ids)):
            raise ValueError("reconciliation has ambiguous reusable step IDs across registries")
        missing_plans = set(proposal.updated_plans) - set(plan_paths)
        missing_steps = set(proposal.updated_reusable_steps) - set(reusable_step_paths) - set(registry_step_ids)
        if missing_plans or missing_steps:
            raise ValueError(
                "reconciliation publication requires paths for affected artifacts: "
                + ", ".join(sorted((*missing_plans, *missing_steps)))
            )
        proposal.repository.validate_persistence()
        targets = {Path(repository_path): yaml.safe_dump(proposal.repository.to_document(), sort_keys=False, allow_unicode=True)}
        for plan_id, plan in proposal.updated_plans.items():
            from automation_harness.core.test_plan import repository_from_plan
            repository_from_plan(plan)
            targets[Path(plan_paths[plan_id])] = yaml.safe_dump(plan.to_dict(), sort_keys=False, allow_unicode=True)
        for step_id, definition in proposal.updated_reusable_steps.items():
            if step_id not in reusable_step_paths:
                continue
            target_path = Path(reusable_step_paths[step_id])
            if target_path in targets:
                raise ValueError("reconciliation has duplicate artifact destination %s" % target_path)
            targets[target_path] = yaml.safe_dump({
                "version": 1, "id": definition.step_id, "name": definition.name,
                "description": definition.description, "inputs": dict(definition.inputs),
                "outputs": dict(definition.outputs), "plan": definition.plan.to_dict(),
            }, sort_keys=False, allow_unicode=True)
        # Project Step Registries contain inline reusable-step plans. Update
        # their documents as one publication target rather than accidentally
        # emitting independent .yaml step files beside the registry.
        for path, registry in step_registries.items():
            updated_steps = tuple(
                proposal.updated_reusable_steps.get(step.step_id, step)
                for step in registry.steps
            )
            if updated_steps == registry.steps:
                continue
            target_path = Path(path)
            if target_path in targets:
                raise ValueError("reconciliation has duplicate artifact destination %s" % target_path)
            updated_registry = replace(registry, steps=updated_steps)
            targets[target_path] = yaml.safe_dump(updated_registry.to_document(), sort_keys=False, allow_unicode=True)
        if expected_fingerprints is None:
            raise ValueError("reconciliation publication requires captured disk fingerprints for every artifact")
        _atomic_publish(targets, expected_fingerprints=expected_fingerprints)


def _rewrite_plan(plan, remapped: Mapping[str, str], repository: ComponentRepository):
    """Update UUID references, then regenerate a valid portable object closure."""
    from automation_harness.core.test_plan import embed_plan_repository

    def contains_remapped(value):
        if isinstance(value, Mapping):
            return any(contains_remapped(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_remapped(item) for item in value)
        return isinstance(value, str) and value in remapped

    if not contains_remapped(plan.to_dict()):
        return plan

    def rewrite(value, key=None):
        if key in {"component_id", "component", "object", "window", "owner_object_id"} and isinstance(value, str):
            return remapped.get(value, value)
        if isinstance(value, Mapping):
            return {item_key: rewrite(item_value, item_key) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if isinstance(value, tuple):
            return tuple(rewrite(item) for item in value)
        return value

    rewritten = replace(plan, steps=tuple(
        replace(call, inputs=rewrite(call.inputs), completion=rewrite(call.completion), scope=rewrite(call.scope))
        for call in plan.steps
    ))
    return embed_plan_repository(rewritten, repository)


def _atomic_publish(targets: Mapping[Path, str], *,
                    expected_fingerprints: Mapping[Path, RepositoryFingerprint]) -> None:
    expected = {Path(path): value for path, value in expected_fingerprints.items()}
    if set(expected) != set(targets):
        raise ValueError("reconciliation requires one baseline fingerprint per target artifact")
    # Validate all baselines before creating temporary files or replacing any
    # artifact. Recheck immediately before each individual replacement.
    for target in targets:
        if repository_fingerprint(target) != expected[target]:
            raise RuntimeError("reconciliation target changed on disk: %s" % target)
    staged = {}
    originals = {}
    replaced = []
    try:
        for target, content in targets.items():
            target.parent.mkdir(parents=True, exist_ok=True)
            originals[target] = target.read_bytes() if target.exists() else None
            descriptor, temporary = tempfile.mkstemp(prefix=".%s." % target.name, suffix=".tmp", dir=str(target.parent), text=True)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            staged[target] = Path(temporary)
        for target, temporary in staged.items():
            if repository_fingerprint(target) != expected[target]:
                raise RuntimeError("reconciliation target changed on disk: %s" % target)
            os.replace(str(temporary), str(target))
            replaced.append(target)
    except Exception:
        for target in reversed(replaced):
            original = originals[target]
            if original is None:
                try:
                    target.unlink()
                except OSError:
                    pass
            else:
                descriptor, temporary = tempfile.mkstemp(prefix=".%s.rollback." % target.name, suffix=".tmp", dir=str(target.parent))
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(original)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, str(target))
        raise
    finally:
        for temporary in staged.values():
            try:
                temporary.unlink()
            except OSError:
                pass


def _merge(target: ComponentDefinition, source: ComponentDefinition) -> ComponentDefinition:
    if target.object_type != source.object_type:
        if target.object_type.value == "custom":
            object_type = source.object_type
        elif source.object_type.value == "custom":
            object_type = target.object_type
        else:
            raise ValueError("cannot reconcile incompatible semantic object types")
    else:
        object_type = target.object_type
    strategies = list(target.strategies)
    for strategy in source.strategies:
        if strategy not in strategies:
            strategies.append(strategy)
    actions = frozenset(set(target.actions) | set(source.actions))
    properties = dict(source.properties)
    properties.update(target.properties)
    scope = dict(source.scope)
    scope.update(target.scope)
    return replace(target, strategies=tuple(strategies), actions=actions, properties=properties, scope=scope,
                   object_type=object_type, revision=max(target.revision, source.revision) + 1)
