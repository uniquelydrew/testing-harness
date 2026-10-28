from uuid import uuid4

import pytest

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.repository_preparation import IdentityAssessment, RepositoryQualifier, RepositoryResolver, definition_fingerprint
from automation_harness.core.repository_scope import OVERRIDE_PROPERTY
from automation_harness.core.repository_reconciliation import RepositoryReconciler
from automation_harness.core.test_plan import embed_plan_repository
from automation_harness.core.test_plan import load_plan
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy
from automation_harness.models.gui import ObjectType
from automation_harness.models.plan import StepCall, TestPlan


def _definition(name="Submit", object_id=None, revision=1, owner=None):
    return ComponentDefinition(
        name, object_id=object_id or str(uuid4()), revision=revision, owner_object_id=owner,
        object_type=ObjectType.BUTTON,
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"accessible_id": name}}}),),
    )


def test_linked_uses_canonical_revision_and_portable_keeps_snapshot():
    original = _definition(revision=1)
    plan = embed_plan_repository(TestPlan(name="mode", steps=(StepCall("use", "gui.object.action", {
        "component_id": original.object_id, "action": {"type": "click"},
    }),)), ComponentRepository({original.component_id: original}))
    updated = ComponentDefinition(**{**original.__dict__, "revision": 2})
    canonical = ComponentRepository({updated.component_id: updated})

    assert RepositoryResolver().resolve(plan, canonical_repository=canonical).repository.get("Submit").revision == 2
    assert RepositoryResolver().resolve(plan, canonical_repository=canonical, mode="portable").repository.get("Submit").revision == 1


def test_locked_rejects_stale_canonical_definition():
    original = _definition(revision=1)
    plan = embed_plan_repository(TestPlan(name="locked", steps=(StepCall("use", "gui.object.action", {
        "component_id": original.object_id, "action": {"type": "click"},
    }),)), ComponentRepository({original.component_id: original}))
    updated = ComponentDefinition(**{**original.__dict__, "revision": 2})
    with pytest.raises(ComponentRepositoryError, match="locked repository resolution failed"):
        RepositoryResolver().resolve(plan, canonical_repository=ComponentRepository({updated.component_id: updated}), mode="locked")


def test_locked_rejects_a_legacy_snapshot_without_provenance():
    with pytest.raises(ComponentRepositoryError, match="no snapshot provenance"):
        RepositoryResolver().resolve(TestPlan(name="legacy"), mode="locked")


def test_linked_resolution_accepts_renamed_local_override_and_translates_old_reference():
    object_id = str(uuid4())
    shared = _definition("FileMenu", object_id=object_id)
    override = ComponentDefinition(**{
        **shared.__dict__, "component_id": "FileMenuForTest",
        "properties": {OVERRIDE_PROPERTY: object_id},
    })
    plan = TestPlan(name="override", steps=(StepCall("use", "gui.object.action", {
        "component_id": "FileMenu", "action": {"type": "click"},
    }),), objects=ComponentRepository({shared.component_id: shared}).to_document()["components"])
    prepared = RepositoryQualifier().qualify(plan, canonical_repository=ComponentRepository({override.component_id: override}))
    assert prepared.issues == ()
    assert prepared.plan.steps[0].inputs["component_id"] == object_id
    assert prepared.repository.get(object_id).component_id == "FileMenuForTest"


def test_fingerprint_ignores_label_but_covers_all_executable_definition_content():
    definition = _definition()
    assert definition_fingerprint(definition) == definition_fingerprint(ComponentDefinition(**{
        **definition.__dict__, "component_id": "Renamed Submit",
    }))
    assert definition_fingerprint(definition) != definition_fingerprint(ComponentDefinition(**{
        **definition.__dict__, "expected_states": {"visible": True},
    }))
    assert definition_fingerprint(definition) != definition_fingerprint(ComponentDefinition(**{
        **definition.__dict__, "action_completion": {"activate": {"mode": "explicit"}},
    }))


def test_qualified_repository_rejects_mutation():
    definition = ComponentDefinition(**{
        **_definition().__dict__, "properties": {"nested": {"value": 1}},
    })
    plan = TestPlan(name="immutable", steps=(StepCall("use", "gui.object.action", {
        "component_id": definition.object_id, "action": {"type": "click"},
    }),), objects=ComponentRepository({definition.component_id: definition}).to_document()["components"])
    prepared = RepositoryQualifier().qualify(plan)
    with pytest.raises(TypeError):
        prepared.repository.components["unexpected"] = _definition()
    with pytest.raises(TypeError):
        prepared.repository.get(definition.object_id).properties["nested"]["value"] = 2


def test_identity_assessment_requires_scope_and_reports_ambiguous_matches():
    one = _definition("One")
    two = _definition("Two")
    # Same durable native identity in one repository is deliberately an
    # authoring conflict: no capture may choose between the two silently.
    two = ComponentDefinition(**{**two.__dict__, "strategies": one.strategies})
    repository = ComponentRepository({one.component_id: one, two.component_id: two})
    capture = CapturedComponent(
        name="Submit", role="push button", description=None, accessible_id="One", application="demo",
        hierarchy=(), actions=("activate",), bounds=None, state=ComponentState(present=True),
        object_type=ObjectType.BUTTON,
    )
    assessment = IdentityAssessment.assess(repository, capture)
    assert assessment.status == "ambiguous"
    assert set(assessment.matches) == {one.object_id, two.object_id}


def test_reconciler_remaps_children_in_one_validated_proposal():
    target, duplicate = _definition("Target"), _definition("Duplicate")
    child = _definition("Child", owner=duplicate.object_id)
    proposal = RepositoryReconciler().propose(
        ComponentRepository({item.component_id: item for item in (target, duplicate, child)}),
        target.object_id, (duplicate.object_id,),
    )
    assert proposal.remapped_object_ids == {duplicate.object_id: target.object_id}
    assert proposal.repository.get("Child").owner_object_id == target.object_id
    assert not proposal.repository.contains("Duplicate")


def test_reconciler_updates_dependent_plan_references_and_snapshots():
    target, duplicate = _definition("Target"), _definition("Duplicate")
    source = ComponentRepository({target.component_id: target, duplicate.component_id: duplicate})
    plan = TestPlan(name="dependent", steps=(StepCall("use", "gui.object.action", {
        "component_id": duplicate.object_id, "action": {"type": "click"},
    }),), objects=source.to_document()["components"])
    proposal = RepositoryReconciler().propose(
        source, target.object_id, (duplicate.object_id,), plans={"dependent.ahplan": plan},
    )
    updated = proposal.updated_plans["dependent.ahplan"]
    assert proposal.affected_plan_ids == ("dependent.ahplan",)
    assert updated.steps[0].inputs["component_id"] == target.object_id
    assert set(updated.objects) == {"Target"}


def test_reconciler_publishes_repository_and_dependent_plan_together(tmp_path):
    target, duplicate = _definition("Target"), _definition("Duplicate")
    source = ComponentRepository({target.component_id: target, duplicate.component_id: duplicate})
    plan = TestPlan(name="dependent", steps=(StepCall("use", "gui.object.action", {
        "component_id": duplicate.object_id, "action": {"type": "click"},
    }),), objects=source.to_document()["components"])
    proposal = RepositoryReconciler().propose(source, target.object_id, (duplicate.object_id,), plans={"plan": plan})
    repository_path = tmp_path / "objects.ahobjects"
    plan_path = tmp_path / "dependent.ahplan"
    source.save(repository_path)
    from automation_harness.core.test_plan import save_plan
    save_plan(plan, plan_path)

    RepositoryReconciler().publish(proposal, repository_path=repository_path, plan_paths={"plan": plan_path})

    assert not ComponentRepository.load((repository_path,)).contains(duplicate.object_id)
    assert load_plan(plan_path).steps[0].inputs["component_id"] == target.object_id


def test_qualifier_returns_fingerprinted_effective_repository():
    prepared = RepositoryQualifier().qualify(TestPlan(name="empty"))
    assert prepared.issues == ()
    assert len(prepared.fingerprint) == 64
