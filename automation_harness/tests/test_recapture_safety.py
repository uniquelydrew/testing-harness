"""Authoring recapture safety independent of live desktop availability."""
from dataclasses import replace
from uuid import uuid4

import pytest

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_capture import (
    ObjectCaptureService,
    RecaptureValidationError,
)
from automation_harness.models.component import (
    CapturedComponent, ComponentState,
)
from automation_harness.models.gui import ObjectType


class OfflineDriver:
    available = False


def captured(**overrides):
    defaults = dict(
        name="Save", role="push button", description="", accessible_id="save",
        application="Example", window="Main", hierarchy=("Toolbar",),
        actions=("click",), bounds=(10, 20, 30, 40),
        state=ComponentState(present=True), backend_properties={},
        framework="atspi", native_class="Button",
    )
    defaults.update(overrides)
    return CapturedComponent(**defaults)


def setup():
    service = ObjectCaptureService(driver=OfflineDriver())
    original = service.definition_from_capture("Save", captured(), validate_live=False)
    return service, ComponentRepository({original.component_id: original}), original


@pytest.mark.parametrize("change,code", [
    ({"role": "check box", "object_type": ObjectType.CHECK_BOX}, "wrong_semantic_type"),
    ({"framework": "javafx"}, "wrong_framework"),
    ({"native_class": "DifferentButton"}, "wrong_native_class"),
    ({"application": "Other Application"}, "wrong_application_scope"),
    ({"window": "Other Window"}, "wrong_window_scope"),
])
def test_recapture_rejects_incompatible_target_before_mutation(change, code):
    service, repository, original = setup()
    with pytest.raises(RecaptureValidationError) as error:
        service.stage_recapture(repository, original, captured(**change), validate_live=False)
    assert error.value.code == code
    assert repository.get(original.object_id) == original


def test_recapture_preserves_identity_behaviors_and_partial_menu_inventory():
    service, repository, original = setup()
    original = replace(
        original,
        actions=frozenset({"activate", "click"}),
        subobjects={"menu": {"text": "File", "subobjects": {"save": {"text": "Save"}}}},
        action_completion={"click": {"mode": "automatic"}},
    )
    repository = ComponentRepository({original.component_id: original})
    candidate = service.stage_recapture(
        repository, original, captured(accessible_id="save-new"), validate_live=False,
    )
    updated = service.apply_recapture_candidate(repository, candidate)
    result = updated.get(original.object_id)

    assert result.object_id == original.object_id
    assert result.revision == original.revision + 1
    assert result.object_type == original.object_type
    assert result.actions == original.actions
    assert result.action_completion == original.action_completion
    assert result.subobjects == original.subobjects
    assert result.strategies[0].options["identification"]["mandatory"]["accessible_id"] == "save-new"
    assert repository.get(original.object_id) == original


def test_staged_capture_cannot_overwrite_more_recent_revision():
    service, repository, original = setup()
    candidate = service.stage_recapture(repository, original, captured(), validate_live=False)
    concurrent = repository.with_component(replace(original, revision=original.revision + 1))
    with pytest.raises(RecaptureValidationError, match="changed while recapture"):
        service.apply_recapture_candidate(concurrent, candidate)
    assert concurrent.get(original.object_id).revision == original.revision + 1


def test_staging_rejects_stale_starting_definition():
    service, repository, original = setup()
    newer = repository.with_component(replace(original, revision=original.revision + 1))
    with pytest.raises(RecaptureValidationError, match="changed after"):
        service.stage_recapture(newer, original, captured(), validate_live=False)


def test_recapture_with_missing_capture_metadata_keeps_existing_ownership():
    service, repository, original = setup()
    original = replace(original, properties={**original.properties, "custom_annotation": "keep"})
    repository = ComponentRepository({original.component_id: original})
    candidate = service.stage_recapture(repository, original, captured(
        accessible_id="save-recaptured", framework=None, native_class=None,
        application=None, window=None,
    ), validate_live=False)
    updated = service.apply_recapture_candidate(repository, candidate).get(original.object_id)
    assert updated.framework == original.framework
    assert updated.native_class == original.native_class
    assert updated.scope["ownership"]["application"] == original.scope["ownership"]["application"]
    assert updated.scope["ownership"]["window"] == original.scope["ownership"]["window"]
    assert updated.properties["custom_annotation"] == "keep"


def test_recapture_rejects_same_revision_definition_mutation():
    service, repository, original = setup()
    candidate = service.stage_recapture(repository, original, captured(), validate_live=False)
    changed = repository.with_component(replace(original, description="external edit"))
    with pytest.raises(RecaptureValidationError, match="changed while recapture"):
        service.apply_recapture_candidate(changed, candidate)
    with pytest.raises(RecaptureValidationError, match="changed after"):
        service.stage_recapture(changed, original, captured(), validate_live=False)
