from dataclasses import replace

import pytest

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.object_reparenting import reparent_leaf
from automation_harness.core.repository_hierarchy import concrete_parent_ids
from automation_harness.models.component import ComponentDefinition, ComponentStrategy


def _object(name, **values):
    return ComponentDefinition(
        component_id=name,
        strategies=(ComponentStrategy("atspi", {
            "identification": {"mandatory": {"accessible_id": name}},
        }),),
        **values
    )


def test_dotted_display_names_do_not_imply_ownership():
    panel = _object("Display.MainPanel")
    button = _object("Display.MainPanel.Save")
    orphan = _object("Logical.Group.Cancel")
    repository = ComponentRepository({item.component_id: item for item in (panel, button, orphan)})

    parents = concrete_parent_ids(repository)
    assert parents[button.object_id] is None
    assert parents[orphan.object_id] is None


def test_owner_round_trips_by_immutable_identity():
    panel = _object("Panel")
    button = _object("Panel.Save", owner_object_id=panel.object_id)
    repository = ComponentRepository({panel.component_id: panel, button.component_id: button})

    loaded = ComponentRepository.from_document(repository.to_document())

    assert loaded.get(button.object_id).owner_object_id == panel.object_id


def test_repository_rejects_missing_owner_and_cycles():
    panel = _object("Panel")
    with pytest.raises(ComponentRepositoryError, match="does not identify"):
        ComponentRepository({"Panel": replace(panel, owner_object_id="fb775e5d-1151-4c0f-b33b-2024e516caef")})

    child = _object("Child", owner_object_id=panel.object_id)
    panel = replace(panel, owner_object_id=child.object_id)
    with pytest.raises(ComponentRepositoryError, match="cyclic ownership"):
        ComponentRepository({"Panel": panel, "Child": child})


def test_delete_reparents_children_and_preserves_child_identity():
    panel = _object("Panel")
    child = _object("Panel.Save", owner_object_id=panel.object_id)
    repository = ComponentRepository({panel.component_id: panel, child.component_id: child})

    updated = repository.without_component("Panel", reparent_children=True)

    assert not updated.contains("Panel")
    assert updated.get("Panel.Save").object_id == child.object_id
    assert updated.get("Panel.Save").owner_object_id is None
    updated.validate_persistence()


def test_delete_rejects_dangling_children_without_explicit_safe_mode():
    panel = _object("Panel")
    child = _object("Panel.Save", owner_object_id=panel.object_id)
    repository = ComponentRepository({panel.component_id: panel, child.component_id: child})

    with pytest.raises(ComponentRepositoryError, match="child object"):
        repository.without_component("Panel")


def test_recoverable_load_isolates_invalid_object_for_workbench_repair(tmp_path):
    path = tmp_path / "broken.ahobjects"
    path.write_text(
        """version: 3
components:
  Good:
    object_id: 4f0d8a3d-1c5a-4e31-8bb8-4e1dbd11df35
    actions: [resolve]
    strategies:
      - type: atspi
        identification:
          mandatory: {accessible_id: good}
  Broken MenuButton:
    object_id: 8b3b6f95-4c56-4a54-a2e4-6c75de43fd1e
    actions: [resolve, activate]
    strategies:
      - type: atspi
        identification:
          mandatory: {}
""",
        encoding="utf-8",
    )

    repository, issues = ComponentRepository.load_recoverable((path,))

    assert repository.contains("Good")
    assert not repository.contains("Broken MenuButton")
    assert any(item[0] == "Broken MenuButton" for item in issues)
    repository.validate_persistence()


def test_visual_leaf_requires_rendering_surface_parent():
    panel = _object("Panel", framework="swing")
    visual = ComponentDefinition(
        component_id="Track",
        strategies=(ComponentStrategy("anchored_visual", {
            "anchor_identification": {"mandatory": {"accessible_id": "surface"}},
            "relative_bounds": [0.1, 0.1, 0.2, 0.2],
        }),),
    )
    repository = ComponentRepository({"Panel": panel, "Track": visual})
    with pytest.raises(ComponentRepositoryError, match="rendering surface"):
        reparent_leaf(repository, "Track", "Panel")

    surface = replace(panel, component_id="Surface", framework="jogl")
    repository = ComponentRepository({"Surface": surface, "Track": visual})
    updated, _, new_id = reparent_leaf(repository, "Track", "Surface")
    assert updated.get(new_id).owner_object_id == surface.object_id


def test_visual_reparent_to_a_new_surface_requires_recapture():
    first = _object("FirstSurface", framework="jogl")
    second = _object("SecondSurface", framework="jogl")
    visual = ComponentDefinition(
        component_id="FirstSurface.Track",
        owner_object_id=first.object_id,
        strategies=(ComponentStrategy("anchored_visual", {
            "anchor_identification": {"mandatory": {"accessible_id": "first"}},
            "relative_bounds": [0.1, 0.1, 0.2, 0.2],
        }),),
        properties={"locator_status": "ready", "coordinate_space": "normalized-owner"},
    )
    repository = ComponentRepository({item.component_id: item for item in (first, second, visual)})

    updated, _, new_id = reparent_leaf(repository, visual.component_id, second.component_id)

    moved = updated.get(new_id)
    assert moved.object_id == visual.object_id
    assert moved.owner_object_id == second.object_id
    assert moved.properties["locator_status"] == "needs_recapture"
    assert moved.properties["previous_owner_object_id"] == first.object_id
