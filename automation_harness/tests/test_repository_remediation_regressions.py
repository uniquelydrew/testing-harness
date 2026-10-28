"""Regression contract: migration, Menu preservation, external-only plans and safe saves."""
from dataclasses import replace
from uuid import uuid4

import pytest

from automation_harness.authoring.plan_repository import assign_repositories
from automation_harness.authoring.project import AuthoringProject
from automation_harness.core.component_repository import (
    ComponentRepository, repository_fingerprint,
)
from automation_harness.core.repository_migration import (
    prepare_embedded_migration, publish_embedded_migration,
)
from automation_harness.core.repository_preparation import RepositoryQualifier, RepositoryResolver
from automation_harness.core.repository_reconciliation import RepositoryReconciler
from automation_harness.core.repository_scope import RepositoryAssociation, RepositoryScope
from automation_harness.core.repository_usage import project_object_usages
from automation_harness.core.reusable_step_expansion import expand_reusable_steps
from automation_harness.core.reusable_step_snapshot import (
    load_snapshotted_reusable_steps, reusable_step_to_snapshot,
)
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.core.test_plan import TestPlanError, load_plan, save_plan
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType
from automation_harness.models.plan import StepCall, TestPlan
from automation_harness.runner.plan_resources import resolve_plan_resources


def object_def(name, *, object_id=None, owner=None, subobjects=None,
               completion=None, expected=None):
    return ComponentDefinition(
        name, object_id=object_id or str(uuid4()),
        owner_object_id=owner, object_type=ObjectType.MENU,
        strategies=(ComponentStrategy(
            "javafx", {"identification": {"mandatory": {"id": name}}}
        ),), actions=frozenset({"resolve", "activate", "select_menu_item"}),
        subobjects=subobjects or {}, action_completion=completion or {},
        expected_states=expected or {},
    )


def menu_action(reference, *, node="select", route=None):
    return StepCall(node, "gui.object.action", {
        "component_id": reference,
        "action": {"type": "select_menu_item", "path": route or "Open"},
    })


def reusable_snapshot(step_id, plan):
    return reusable_step_to_snapshot(ReusableStepDefinition(
        step_id=step_id, name=step_id, description="",
        plan=plan, inputs={}, outputs={},
    ))


def test_menu_merge_preserves_distinct_routes_and_completion_contracts():
    target = object_def("Canonical", subobjects={
        "open": {"kind": "menu_item", "selector": {"criteria": {"id": "open"}}},
    }, completion={"activate": {"mode": "automatic"}},
        expected={"visible": True})
    duplicate = object_def("Duplicate", subobjects={
        "camera": {"kind": "menu_item", "selector": {"criteria": {"id": "camera"}}},
    }, completion={"select_menu_item": {"mode": "automatic"}},
        expected={"enabled": True})
    result = RepositoryReconciler().propose(
        ComponentRepository({target.component_id: target,
                             duplicate.component_id: duplicate}),
        target.object_id, (duplicate.object_id,),
    ).repository.get(target.object_id)
    assert set(result.subobjects) == {"open", "camera"}
    assert set(result.action_completion) == {"activate", "select_menu_item"}
    assert result.expected_states == {"visible": True, "enabled": True}


def test_menu_merge_rejects_colliding_subobject_route():
    target = object_def("Canonical", subobjects={
        "open": {"kind": "menu_item", "selector": {"criteria": {"id": "open"}}},
    })
    duplicate = object_def("Duplicate", subobjects={
        "open": {"kind": "menu_item", "selector": {"criteria": {"id": "other"}}},
    })
    with pytest.raises(ValueError, match="conflicting subobjects"):
        RepositoryReconciler().propose(
            ComponentRepository({target.component_id: target,
                                 duplicate.component_id: duplicate}),
            target.object_id, (duplicate.object_id,),
        )


def test_canonical_migration_and_followup_merge_never_reembed_objects(tmp_path, monkeypatch):
    target = object_def("Canonical")
    duplicate = object_def("Duplicate")
    old = ComponentRepository({duplicate.component_id: duplicate})
    canonical = ComponentRepository({
        target.component_id: target, duplicate.component_id: duplicate,
    })
    plan_path = tmp_path / "scenario.ahplan"
    repo_path = tmp_path / "objects.ahobjects"
    canonical.save(repo_path)
    plan = TestPlan(name="scenario", steps=(menu_action(duplicate.object_id),),
                    objects=old.to_document()["components"])
    save_plan(plan, plan_path)
    baselines = {x: repository_fingerprint(x) for x in (plan_path, repo_path)}
    migrated = prepare_embedded_migration(plan, plan_path, canonical, repo_path)
    publish_embedded_migration(
        migrated, plan_path=plan_path, repository_path=repo_path,
        expected_fingerprints=baselines,
    )
    loaded = load_plan(plan_path)
    assert loaded.objects == {}
    assert loaded.execution_mode == "canonical"
    merged = RepositoryReconciler().propose(
        ComponentRepository.load((repo_path,)), target.object_id, (duplicate.object_id,),
        plans={"scenario": loaded},
    )
    rewritten = merged.updated_plans["scenario"]
    assert rewritten.objects == {}
    assert rewritten.steps[0].inputs["component_id"] == target.object_id
    before_merge = {x: repository_fingerprint(x) for x in (plan_path, repo_path)}
    RepositoryReconciler().publish(
        merged, repository_path=repo_path, plan_paths={"scenario": plan_path},
        expected_fingerprints=before_merge,
    )
    reloaded = load_plan(plan_path)
    assert reloaded.execution_mode == "canonical" and not reloaded.objects
    prepared = RepositoryQualifier().qualify(
        reloaded, canonical_repository=ComponentRepository.load((repo_path,)),
    )
    assert not prepared.issues
    assert prepared.plan.steps[0].inputs["component_id"] == target.object_id
    assert prepared.repository.contains(target.object_id)
    assert not prepared.repository.contains(duplicate.object_id)

    # Execute the reloaded plan through the real managed runner/registered
    # gui.object.action step, substituting only the native desktop boundary.
    from automation_harness.backends.base import ExecutionBackend
    from automation_harness.core.test_context import TestContext
    from automation_harness.models.run import BackendHealth
    from automation_harness.runner.plan_execution import execute_plan

    observed = []

    def fake_component(context, reference):
        definition = context.components.get(reference)
        observed.append(definition.object_id)

        class NativeHandle:
            def execute(self, action, *, strategy=None):
                return {"strategy": "javafx", "action": action.type.value}

        return NativeHandle()

    class HeadlessComponentBackend(ExecutionBackend):
        name = "headless-component"

        @property
        def capabilities(self):
            return {"components"}

        @property
        def allowed_step_risks(self):
            return frozenset({"read_only", "application_control"})

        def start(self, *, run_dir):
            return {}

        def health_check(self):
            return BackendHealth(True, self.name, {})

        def stop(self):
            return None

    monkeypatch.setattr(TestContext, "component", fake_component)
    executed = execute_plan(
        reloaded, HeadlessComponentBackend(),
        runs_dir=tmp_path / "Test Runs",
        component_repository=ComponentRepository.load((repo_path,)),
    )
    assert executed.exit_code == 0
    assert executed.passed == 1
    assert observed == [target.object_id]


def test_migration_rewrites_reusable_step_snapshot_and_validates_expansion(tmp_path):
    existing = object_def("Existing")
    duplicate = object_def("Old")
    repo = ComponentRepository({existing.component_id: existing})
    embedded = ComponentRepository({duplicate.component_id: duplicate})
    inner = TestPlan(name="inner", steps=(menu_action("Old"),),
                     objects=embedded.to_document()["components"])
    plan = TestPlan(name="parent", steps=(StepCall("run", "shared.menu", {}),),
        objects=embedded.to_document()["components"],
        step_definitions={"shared.menu": reusable_snapshot("shared.menu", inner)})
    proposal = prepare_embedded_migration(
        plan, tmp_path / "parent.ahplan", repo, tmp_path / "objects.ahobjects",
        resolutions={duplicate.object_id: existing.object_id},
    )
    assert proposal.plan.objects == {}
    nested = load_snapshotted_reusable_steps(proposal.plan)
    assert nested["shared.menu"].plan.objects == {}
    assert nested["shared.menu"].plan.steps[0].inputs["component_id"] == existing.object_id
    expanded = expand_reusable_steps(proposal.plan, nested)
    assert expanded.steps[0].inputs["component_id"] == existing.object_id
    assert not RepositoryQualifier().qualify(
        expanded, canonical_repository=proposal.repository,
    ).issues


def test_migration_rejects_invalid_reusable_step_reference(tmp_path):
    canonical = object_def("Canonical")
    old = object_def("Old")
    bad_inner = TestPlan(name="inner", steps=(menu_action(str(uuid4())),))
    plan = TestPlan(name="parent", steps=(StepCall("run", "shared.menu", {}),),
        objects=ComponentRepository({old.component_id: old}).to_document()["components"],
        step_definitions={"shared.menu": reusable_snapshot("shared.menu", bad_inner)})
    with pytest.raises(Exception, match="missing canonical object|unknown component"):
        prepare_embedded_migration(
            plan, tmp_path / "plan.ahplan",
            ComponentRepository({canonical.component_id: canonical}),
            tmp_path / "objects.ahobjects",
        )


@pytest.mark.parametrize("mode", ["portable", "locked"])
def test_resource_discovery_respects_snapshot_mode_despite_external_name_conflict(tmp_path, mode):
    from automation_harness.core.test_plan import embed_plan_repository
    external, snapshot = object_def("File"), object_def("File")
    repo_path = tmp_path / "objects.ahobjects"
    plan_path = tmp_path / "plan.ahplan"
    ComponentRepository({"File": external}).save(repo_path)
    embedded = embed_plan_repository(
        TestPlan(name="snapshot", steps=(menu_action(snapshot.object_id),)),
        ComponentRepository({"File": snapshot}),
    )
    plan = assign_repositories(
        replace(embedded, execution_mode=mode), plan_path,
        (RepositoryAssociation(repo_path, RepositoryScope.LOCAL),),
    )
    if mode == "portable":
        resolved = resolve_plan_resources(plan, plan_path)
        assert resolved.repository.get("File").object_id == snapshot.object_id
    else:
        from automation_harness.core.component_repository import ComponentRepositoryError
        # Locked plans correctly reject revision/identity mismatch with the
        # external definition rather than quietly selecting a linked copy.
        with pytest.raises(ComponentRepositoryError):
            resolve_plan_resources(plan, plan_path)


def test_guarded_plan_save_rejects_concurrent_replacement(tmp_path):
    path = tmp_path / "test.ahplan"
    original = TestPlan(name="Initial")
    save_plan(original, path)
    baseline = repository_fingerprint(path)
    save_plan(TestPlan(name="External"), path)
    external_bytes = path.read_bytes()
    with pytest.raises(TestPlanError, match="changed on disk"):
        save_plan(TestPlan(name="Stale"), path, expected_fingerprint=baseline)
    assert path.read_bytes() == external_bytes
    assert load_plan(path).name == "External"
    saved = repository_fingerprint(path)
    save_plan(TestPlan(name="Current"), path, expected_fingerprint=saved)
    assert load_plan(path).name == "Current"


def test_usage_index_includes_nested_snapshots_and_owner_relations(tmp_path):
    owner = object_def("MenuOwner")
    child = object_def("Child", owner=owner.object_id)
    repo = ComponentRepository({owner.component_id: owner, child.component_id: child})
    repo_path = tmp_path / "objects.ahobjects"
    repo.save(repo_path)
    plan_path = tmp_path / "nested.ahplan"
    inner = TestPlan(name="inner", steps=(menu_action(child.object_id),))
    outer = TestPlan(name="outer", steps=(StepCall("run", "shared.menu", {}),),
        step_definitions={"shared.menu": reusable_snapshot("shared.menu", inner)})
    outer = assign_repositories(outer, plan_path, (
        RepositoryAssociation(repo_path, RepositoryScope.LOCAL),
    ))
    save_plan(outer, plan_path)
    project = AuthoringProject(name="Project", root=tmp_path, test_plans=(plan_path,))
    uses = project_object_usages(project, repo_path, repo)
    assert any(u.object_id == child.object_id and "snapshot:" in u.source for u in uses)
    assert any(u.object_id == owner.object_id and "ownership" in u.source for u in uses)


def test_portable_reconcile_keeps_transitive_reusable_object_closure(tmp_path):
    target = object_def("Canonical")
    duplicate = object_def("Duplicate")
    repository = ComponentRepository({
        target.component_id: target, duplicate.component_id: duplicate,
    })
    inner = TestPlan(name="nested", steps=(menu_action(duplicate.object_id),),
                     objects={"Duplicate": repository.to_document()["components"]["Duplicate"]})
    outer = TestPlan(
        name="portable-nested", execution_mode="portable",
        steps=(StepCall("nested", "shared.menu", {}),),
        objects={"Duplicate": repository.to_document()["components"]["Duplicate"]},
        step_definitions={"shared.menu": reusable_snapshot("shared.menu", inner)},
    )
    proposal = RepositoryReconciler().propose(
        repository, target.object_id, (duplicate.object_id,),
        plans={"portable": outer},
    )
    updated = proposal.updated_plans["portable"]
    assert updated.execution_mode == "portable"
    assert set(updated.objects) == {"Canonical"}
    assert load_snapshotted_reusable_steps(updated)["shared.menu"].plan.steps[0].inputs[
        "component_id"
    ] == target.object_id
    path = tmp_path / "portable.ahplan"
    save_plan(updated, path)
    reloaded = load_plan(path)
    standalone = RepositoryResolver().resolve(reloaded, mode="portable")
    assert standalone.repository.contains(target.object_id)
    assert not standalone.repository.contains(duplicate.object_id)
    nested = load_snapshotted_reusable_steps(reloaded)
    assert not RepositoryQualifier().qualify(
        reloaded, reusable_steps=nested, mode="portable",
    ).issues
