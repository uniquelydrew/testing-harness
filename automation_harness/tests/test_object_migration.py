"""Tests for in-memory object ownership transfer."""
from uuid import uuid4

import pytest

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.object_migration import reconcile_object, persist_migration, detect_legacy_objects, stage_legacy_migration, stage_reconciled_migration
from automation_harness.core.test_plan import load_plan
from automation_harness.models.plan import TestPlan, StepCall


def object_data(uid):
    return {
        "object_id": uid,
        "object_type": "custom",
        "actions": ["resolve"],
        "strategies": [{"driver": "atspi", "selector": {"name": "File"}}],
    }


def test_migration_removes_embedded_definition_and_rewrites_reference():
    uid = str(uuid4())
    plan = TestPlan(name="demo", objects={"File Menu": object_data(uid)},
                    steps=(StepCall(node_id="one", step_id="gui.interact",
                                    inputs={"component_id": "File Menu"}),))
    updated, repository = reconcile_object(plan, ComponentRepository({}), "File Menu")
    assert updated.objects == {}
    assert updated.steps[0].inputs["component_id"] == uid
    assert repository.get(uid).component_id == "File Menu"


def test_migration_rejects_identity_collision_without_mutating_inputs():
    plan_id, repo_id = str(uuid4()), str(uuid4())
    plan = TestPlan(name="demo", objects={"File Menu": object_data(plan_id)})
    repository = ComponentRepository.from_document({
        "version": 3, "components": {"File Menu": object_data(repo_id)}
    })
    with pytest.raises(ComponentRepositoryError, match="different immutable identities"):
        reconcile_object(plan, repository, "File Menu")
    assert "File Menu" in plan.objects


def test_combination_requires_selection_for_differing_properties():
    uid = str(uuid4())
    source = object_data(uid)
    source["description"] = "recorded"
    destination = object_data(uid)
    destination["description"] = "repository"
    plan = TestPlan(name="demo", objects={"File Menu": source})
    repository = ComponentRepository.from_document({
        "version": 3, "components": {"File Menu": destination}
    })
    with pytest.raises(ValueError, match="requires an explicit selection"):
        reconcile_object(plan, repository, "File Menu", selection="combine", fields={})
    updated, merged = reconcile_object(plan, repository, "File Menu",
                                       selection="combine", fields={"description": "plan"})
    assert updated.objects == {}
    assert merged.get("File Menu").description == "recorded"


def test_persist_migration_removes_plan_definition(tmp_path):
    uid = str(uuid4())
    plan = TestPlan(name="demo", objects={"File Menu": object_data(uid)})
    updated, repository = reconcile_object(plan, ComponentRepository({}), "File Menu")
    plan_path = tmp_path / "demo.ahplan"
    repo_path = tmp_path / "demo.ahobjects"
    persist_migration(plan_path, repo_path, updated, repository)
    assert load_plan(plan_path).objects == {}
    assert ComponentRepository.load((repo_path,)).get(uid).component_id == "File Menu"


def test_persist_migration_rejects_overlap_before_writing(tmp_path):
    uid = str(uuid4())
    plan = TestPlan(name="demo", objects={"File Menu": object_data(uid)})
    repository = ComponentRepository.from_document({
        "version": 3, "components": {"File Menu": object_data(uid)}
    })
    plan_path = tmp_path / "demo.ahplan"
    repo_path = tmp_path / "demo.ahobjects"
    with pytest.raises(ComponentRepositoryError, match="ownership conflict"):
        persist_migration(plan_path, repo_path, plan, repository)
    assert not plan_path.exists()
    assert not repo_path.exists()


def test_bulk_legacy_migration_preserves_parent_child_ownership():
    parent_id, child_id = str(uuid4()), str(uuid4())
    parent = object_data(parent_id)
    child = object_data(child_id)
    child["owner_object_id"] = parent_id
    plan = TestPlan(name="legacy", objects={"Window": parent, "Button": child},
                    steps=(StepCall(node_id="one", step_id="gui.interact",
                                    inputs={"component_id": "Button"}),))
    migrated, repository = stage_legacy_migration(plan, ComponentRepository({}))
    assert migrated.objects == {}
    assert migrated.steps[0].inputs["component_id"] == child_id
    assert repository.get(child_id).owner_object_id == parent_id


def test_bulk_migration_never_auto_resolves_identity_conflicts():
    uid = str(uuid4())
    plan = TestPlan(name="legacy", objects={"File Menu": object_data(uid)})
    repository = ComponentRepository.from_document({
        "version": 3, "components": {"File Menu": object_data(uid)}
    })
    safe, conflicts = detect_legacy_objects(plan, repository)
    assert not safe
    assert conflicts
    with pytest.raises(ComponentRepositoryError, match="explicit reconciliation"):
        stage_legacy_migration(plan, repository)


def test_reconciled_migration_accepts_existing_destination_uuid():
    uid = str(uuid4())
    plan = TestPlan(name="legacy", objects={"File Menu": object_data(uid)},
                    steps=(StepCall(node_id="one", step_id="gui.interact",
                                    inputs={"component_id": "File Menu"}),
                           StepCall(node_id="two", step_id="gui.interact",
                                    inputs={"component_id": uid})))
    repository = ComponentRepository.from_document({
        "version": 3, "components": {"File Menu": object_data(uid)}
    })
    migrated, updated_repository = stage_reconciled_migration(
        plan, repository, {"File Menu": {"selection": "repository"}}
    )
    assert migrated.objects == {}
    assert [step.inputs["component_id"] for step in migrated.steps] == [uid, uid]
    assert updated_repository.get(uid).component_id == "File Menu"
