from __future__ import annotations

import pytest

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.object_reparenting import reparent_leaf
from automation_harness.models.component import ComponentDefinition, ComponentStrategy


def _object(name, revision=1):
    return ComponentDefinition(
        component_id=name,
        revision=revision,
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"accessible_id": name}}}),),
    )


def test_reparent_leaf_preserves_immutable_identity_and_increments_revision():
    parent = _object("MainPanel")
    leaf = _object("SaveButton", revision=3)
    repository = ComponentRepository({parent.component_id: parent, leaf.component_id: leaf})
    updated, old_id, new_id = reparent_leaf(repository, "SaveButton", "MainPanel")
    assert old_id == "SaveButton"
    assert new_id == "MainPanel.SaveButton"
    assert "SaveButton" not in updated.components
    moved = updated.get(new_id)
    assert moved.object_id == leaf.object_id
    assert moved.revision == 4


def test_reparent_requires_concrete_repository_parent():
    repository = ComponentRepository({"SaveButton": _object("SaveButton")})
    with pytest.raises(ComponentRepositoryError):
        reparent_leaf(repository, "SaveButton", "LogicalGroup")


def test_reparent_rejects_non_leaf_source():
    repository = ComponentRepository({
        "Panel": _object("Panel"),
        "Panel.Save": _object("Panel.Save"),
        "Other": _object("Other"),
    })
    with pytest.raises(ComponentRepositoryError, match="only repository leaves"):
        reparent_leaf(repository, "Panel", "Other")


def test_reparent_rejects_name_collision():
    repository = ComponentRepository({
        "Save": _object("Save"),
        "Panel": _object("Panel"),
        "Panel.Save": _object("Panel.Save"),
    })
    with pytest.raises(ComponentRepositoryError, match="already exists"):
        reparent_leaf(repository, "Save", "Panel")
