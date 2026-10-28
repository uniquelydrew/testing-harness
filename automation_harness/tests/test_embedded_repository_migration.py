"""Embedded Object Repository promotion and canonical-only execution behavior."""
from dataclasses import replace
from uuid import uuid4

import pytest

from automation_harness.core.component_repository import (
    ComponentRepository, ComponentRepositoryError, repository_fingerprint,
)
from automation_harness.core.repository_migration import (
    inspect_embedded_repository, prepare_embedded_migration, publish_embedded_migration,
)
from automation_harness.core.repository_preparation import RepositoryResolver
from automation_harness.core.test_plan import load_plan, save_plan
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType
from automation_harness.models.plan import TestPlan, StepCall
from automation_harness.authoring.plan_repository import assigned_repository_path
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.core.repository_usage import project_object_usages


def _object(name, *, object_id=None, owner=None):
    return ComponentDefinition(
        name, object_id=object_id or str(uuid4()), owner_object_id=owner,
        object_type=ObjectType.BUTTON,
        strategies=(ComponentStrategy(
            "atspi", {"identification": {"mandatory": {"accessible_id": name}}}
        ),),
    )


def _plan(repository, name="plan"):
    return TestPlan(name=name, steps=(StepCall(
        "action", "gui.object.action",
        {"component_id": list(repository.components.values())[-1].object_id,
         "action": {"type": "click"}},
    ),), objects=repository.to_document()["components"])


def test_embedded_inspection_does_not_mutate_plan_or_canonical():
    same = _object("Canonical")
    renamed = replace(same, component_id="OldLabel")
    conflict = _object("InUse")
    newer = _object("InUse")
    fresh = _object("New")
    embedded = ComponentRepository({item.component_id: item for item in (renamed, newer, fresh)})
    canonical = ComponentRepository({same.component_id: same, conflict.component_id: conflict})
    plan = _plan(embedded)
    inspection = inspect_embedded_repository(plan, canonical)
    status = {item.name: item.status for item in inspection.objects}
    assert status == {"OldLabel": "canonical_uuid", "InUse": "name_conflict", "New": "new"}
    assert inspection.conflicting_count == 1
    assert len(plan.objects) == 3
    assert len(canonical.components) == 2


def test_unresolved_name_collision_refuses_implicit_identity_merge(tmp_path):
    canonical = ComponentRepository({"Submit": _object("Submit")})
    local = ComponentRepository({"Submit": _object("Submit")})
    plan = _plan(local)
    with pytest.raises(ValueError, match="conflicts with canonical name"):
        prepare_embedded_migration(plan, tmp_path / "plan.ahplan",
                                   canonical, tmp_path / "objects.ahobjects")


def test_explicit_coalesce_updates_uuid_and_removes_embedded_copy(tmp_path):
    target, source = _object("Canonical"), _object("Submit")
    canonical = ComponentRepository({target.component_id: target})
    embedded = ComponentRepository({source.component_id: source})
    plan_path = tmp_path / "example.ahplan"
    repo_path = tmp_path / "objects.ahobjects"
    plan = _plan(embedded)
    proposal = prepare_embedded_migration(
        plan, plan_path, canonical, repo_path,
        resolutions={source.object_id: target.object_id},
    )
    assert proposal.remapped_ids[source.object_id] == target.object_id
    assert proposal.plan.objects == {}
    assert proposal.plan.execution_mode == "canonical"
    assert proposal.plan.steps[0].inputs["component_id"] == target.object_id
    assert assigned_repository_path(proposal.plan, plan_path) == repo_path
    assert proposal.repository.get(target.object_id).object_id == target.object_id
    assert len(proposal.repository.components) == 1


def test_name_collision_can_be_resolved_by_renaming_import(tmp_path):
    target, source = _object("Submit"), _object("Submit")
    plan = _plan(ComponentRepository({source.component_id: source}))
    proposal = prepare_embedded_migration(
        plan, tmp_path / "plan.ahplan",
        ComponentRepository({target.component_id: target}),
        tmp_path / "repository.ahobjects",
        renames={source.object_id: "LegacySubmit"},
    )
    assert proposal.repository.get(source.object_id).component_id == "LegacySubmit"
    assert proposal.plan.steps[0].inputs["component_id"] == source.object_id
    assert proposal.plan.objects == {}


def test_imported_child_reparents_to_canonical_parent(tmp_path):
    parent = _object("Parent")
    old_parent = _object("OldParent")
    child = _object("Child", owner=old_parent.object_id)
    embedded = ComponentRepository({
        old_parent.component_id: old_parent, child.component_id: child,
    })
    plan = _plan(embedded)
    proposal = prepare_embedded_migration(
        plan, tmp_path / "plan.ahplan", ComponentRepository({parent.component_id: parent}),
        tmp_path / "repository.ahobjects",
        resolutions={old_parent.object_id: parent.object_id},
    )
    assert proposal.repository.get(child.object_id).owner_object_id == parent.object_id


def test_published_plan_resolves_only_against_external_repository(tmp_path):
    object_def = _object("Click")
    embedded = ComponentRepository({object_def.component_id: object_def})
    plan = _plan(embedded)
    repo_path = tmp_path / "objects.ahobjects"
    plan_path = tmp_path / "plan.ahplan"
    ComponentRepository({}).save(repo_path)
    save_plan(plan, plan_path)
    baselines = {path: repository_fingerprint(path) for path in (repo_path, plan_path)}
    proposal = prepare_embedded_migration(
        plan, plan_path, ComponentRepository({}), repo_path,
    )
    publish_embedded_migration(
        proposal, repository_path=repo_path, plan_path=plan_path,
        expected_fingerprints=baselines,
    )
    loaded = load_plan(plan_path)
    assert loaded.objects == {}
    assert loaded.execution_mode == "canonical"
    assert assigned_repository_path(loaded, plan_path) == repo_path
    with pytest.raises(ComponentRepositoryError, match="requires an assigned external"):
        RepositoryResolver().resolve(loaded)
    canonical = ComponentRepository.load((repo_path,))
    qualified = RepositoryResolver().resolve(loaded, canonical_repository=canonical)
    assert qualified.repository.contains(object_def.object_id)


def test_external_mutation_blocks_publishing_both_files(tmp_path):
    object_def = _object("Click")
    embedded = ComponentRepository({object_def.component_id: object_def})
    plan = _plan(embedded)
    repo_path = tmp_path / "objects.ahobjects"
    plan_path = tmp_path / "plan.ahplan"
    ComponentRepository({}).save(repo_path)
    save_plan(plan, plan_path)
    baselines = {path: repository_fingerprint(path) for path in (repo_path, plan_path)}
    proposal = prepare_embedded_migration(
        plan, plan_path, ComponentRepository({}), repo_path,
    )
    plan_path.write_text(plan_path.read_text() + "\n# another author changed this\n")
    before = repo_path.read_bytes()
    with pytest.raises(RuntimeError, match="changed on disk"):
        publish_embedded_migration(
            proposal, repository_path=repo_path, plan_path=plan_path,
            expected_fingerprints=baselines,
        )
    assert repo_path.read_bytes() == before
    assert load_plan(plan_path).objects

def test_project_membership_publishes_with_repository_and_plan(tmp_path):
    definition = _object("Click")
    repo_path = tmp_path / "objects.ahobjects"
    plan_path = tmp_path / "plan.ahplan"
    project_path = tmp_path / "project.ahproject"
    plan = _plan(ComponentRepository({definition.component_id: definition}))
    ComponentRepository({}).save(repo_path)
    save_plan(plan, plan_path)
    project = AuthoringProject(name="Migration Project", root=tmp_path)
    save_authoring_project(project_path, project)
    target_project = project.with_test_plan(plan_path).with_object_repository(repo_path)
    baseline = {path: repository_fingerprint(path)
                for path in (repo_path, plan_path, project_path)}
    proposal = prepare_embedded_migration(plan, plan_path, ComponentRepository({}), repo_path)
    publish_embedded_migration(
        proposal, plan_path=plan_path, repository_path=repo_path,
        project_path=project_path, project=target_project,
        expected_fingerprints=baseline,
    )
    loaded_project = AuthoringProject.load(project_path)
    assert loaded_project.test_plans == (plan_path,)
    assert loaded_project.object_repositories == (repo_path,)
    loaded_plan = load_plan(plan_path)
    usages = project_object_usages(
        loaded_project, repo_path, ComponentRepository.load((repo_path,)),
    )
    assert len(usages) == 1
    assert usages[0].object_id == definition.object_id
    assert usages[0].source == "test plan"


def test_reverse_usage_index_sees_embedded_reference_in_other_plan(tmp_path):
    definition = _object("Click")
    repository = ComponentRepository({definition.component_id: definition})
    path = tmp_path / "unmigrated.ahplan"
    save_plan(_plan(repository), path)
    project = AuthoringProject(name="Uses", root=tmp_path, test_plans=(path,))
    usages = project_object_usages(project, tmp_path / "objects.ahobjects", repository)
    assert len(usages) == 1
    assert usages[0].invocation == "action"

def test_changed_embedded_definition_uses_canonical_uuid_without_overwriting(tmp_path):
    original = _object("Submit")
    stale = replace(original, revision=original.revision + 1, description="stale authoring copy")
    canonical = ComponentRepository({original.component_id: original})
    plan = _plan(ComponentRepository({stale.component_id: stale}))
    inspection = inspect_embedded_repository(plan, canonical)
    assert inspection.objects[0].status == "canonical_changed"
    proposal = prepare_embedded_migration(
        plan, tmp_path / "test.ahplan", canonical, tmp_path / "objects.ahobjects",
    )
    assert proposal.repository.get(original.object_id).revision == original.revision
    assert proposal.plan.steps[0].inputs["component_id"] == original.object_id


def test_same_uuid_incompatible_type_requires_explicit_resolution(tmp_path):
    original = _object("Submit")
    incompatible = replace(original, object_type=ObjectType.CHECK_BOX)
    canonical = ComponentRepository({original.component_id: original})
    plan = _plan(ComponentRepository({incompatible.component_id: incompatible}))
    assert inspect_embedded_repository(plan, canonical).objects[0].status == "type_conflict"
    with pytest.raises(ValueError, match="conflicting canonical semantic type"):
        prepare_embedded_migration(
            plan, tmp_path / "test.ahplan", canonical, tmp_path / "objects.ahobjects",
        )

def test_exact_locator_match_requires_explicit_identity_decision(tmp_path):
    canonical_obj = _object("CanonicalName")
    source = replace(_object("OtherName"), strategies=canonical_obj.strategies)
    canonical = ComponentRepository({canonical_obj.component_id: canonical_obj})
    plan = _plan(ComponentRepository({source.component_id: source}))
    inspection = inspect_embedded_repository(plan, canonical)
    assert inspection.objects[0].status == "possible_duplicate"
    assert inspection.objects[0].candidates == (canonical_obj.object_id,)
    args = (plan, tmp_path / "plan.ahplan", canonical, tmp_path / "objects.ahobjects")
    with pytest.raises(ValueError, match="exact locator match"):
        prepare_embedded_migration(*args)
    mapped = prepare_embedded_migration(
        *args, resolutions={source.object_id: canonical_obj.object_id},
    )
    assert len(mapped.repository.components) == 1
    assert mapped.plan.steps[0].inputs["component_id"] == canonical_obj.object_id
    distinct = prepare_embedded_migration(
        *args, approved_distinct=(source.object_id,),
    )
    assert len(distinct.repository.components) == 2
    assert distinct.plan.steps[0].inputs["component_id"] == source.object_id
