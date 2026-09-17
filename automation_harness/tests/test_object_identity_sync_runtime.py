import pytest

from automation_harness.authoring.object_identity_sync_runtime import _capture_update_target


def test_new_capture_cannot_implicitly_rename_existing_object():
    with pytest.raises(ValueError, match="cannot implicitly rename or replace"):
        _capture_update_target("NewSuggestedName", ("ExistingObject",))


def test_explicit_edit_can_rename_existing_object():
    assert _capture_update_target(
        "NewName", ("ExistingObject",), explicit_old="ExistingObject"
    ) == "ExistingObject"


def test_same_named_capture_updates_without_collapsing_repository():
    assert _capture_update_target("ExistingObject", ("ExistingObject",)) == "ExistingObject"
