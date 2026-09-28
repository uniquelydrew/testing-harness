from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.repository_hierarchy import (
    authoring_parent_ids,
    is_authoring_visible,
)
from automation_harness.core.repository_normalization import (
    plan_repository_normalization,
)
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType


def _definition(
    component_id,
    object_type,
    *,
    native_class=None,
    identity=None,
    owner=None,
):
    return ComponentDefinition(
        component_id=component_id,
        strategies=(
            ComponentStrategy(
                "java_agent",
                {"identification": {"mandatory": dict(identity or {})}},
            ),
        ),
        object_type=object_type,
        native_class=native_class,
        framework="swing",
        owner_object_id=owner,
    )


def test_normalization_hides_layout_panel_and_reparents_visible_descendant():
    window = _definition(
        "MainWindow",
        ObjectType.WINDOW,
        native_class="javax.swing.JFrame",
        identity={"name": "Main"},
    )
    panel = _definition(
        "MainWindow.JPanel",
        ObjectType.PANEL,
        native_class="javax.swing.JPanel",
        owner=window.object_id,
    )
    button = _definition(
        "MainWindow.JPanel.cancelButton",
        ObjectType.BUTTON,
        native_class="javax.swing.JButton",
        identity={"name": "Cancel"},
        owner=panel.object_id,
    )
    repository = ComponentRepository({
        item.component_id: item for item in (window, panel, button)
    })

    plan = plan_repository_normalization(repository)

    assert plan.renames == {
        "MainWindow": "Main Window",
        "MainWindow.JPanel.cancelButton": "Cancel Button",
    }
    normalized_window = plan.repository.get("Main Window")
    normalized_panel = plan.repository.get("MainWindow.JPanel")
    normalized_button = plan.repository.get("Cancel Button")
    assert normalized_window.object_id == window.object_id
    assert normalized_panel.object_id == panel.object_id
    assert normalized_button.object_id == button.object_id
    assert normalized_panel.properties["authoring_visibility"] == "structural_only"
    assert normalized_button.owner_object_id == normalized_window.object_id
    assert is_authoring_visible(normalized_panel) is False
    assert authoring_parent_ids(plan.repository)[normalized_button.object_id] == normalized_window.object_id


def test_named_panel_with_meaningful_identity_remains_visible_fallback():
    window = _definition(
        "MainWindow",
        ObjectType.WINDOW,
        native_class="javax.swing.JFrame",
        identity={"name": "Main"},
    )
    panel = _definition(
        "MainWindow.resultsPanel",
        ObjectType.PANEL,
        native_class="javax.swing.JPanel",
        identity={"accessible_id": "resultsPanel", "name": "Results"},
        owner=window.object_id,
    )
    repository = ComponentRepository({
        item.component_id: item for item in (window, panel)
    })

    plan = plan_repository_normalization(repository)

    assert plan.repository.get("Results Panel").object_id == panel.object_id
    assert is_authoring_visible(plan.repository.get("Results Panel")) is True
    assert plan.structural_only == ()


def test_normalization_uses_numeric_suffixes_for_semantic_name_collisions():
    first = _definition(
        "dialog.firstCancel",
        ObjectType.BUTTON,
        native_class="javax.swing.JButton",
        identity={"name": "Cancel"},
    )
    second = _definition(
        "dialog.secondCancel",
        ObjectType.BUTTON,
        native_class="javax.swing.JButton",
        identity={"name": "Cancel"},
    )
    repository = ComponentRepository({
        item.component_id: item for item in (first, second)
    })

    plan = plan_repository_normalization(repository)

    assert set(plan.repository.components) == {"Cancel Button", "Cancel Button 2"}
    assert {
        plan.repository.get("Cancel Button").object_id,
        plan.repository.get("Cancel Button 2").object_id,
    } == {first.object_id, second.object_id}


def test_normalization_retains_hidden_structural_alias_for_locator_compatibility():
    panel = _definition(
        "MainWindow.JPanel",
        ObjectType.PANEL,
        native_class="javax.swing.JPanel",
    )
    repository = ComponentRepository({panel.component_id: panel})

    plan = plan_repository_normalization(repository)

    assert "MainWindow.JPanel" in plan.repository.components
    assert plan.renames == {}
    assert plan.structural_only == ("MainWindow.JPanel",)


def test_normalization_is_idempotent_after_first_apply():
    button = _definition(
        "some.deep.cancelButton",
        ObjectType.BUTTON,
        native_class="javax.swing.JButton",
        identity={"name": "Cancel"},
    )
    repository = ComponentRepository({button.component_id: button})

    first = plan_repository_normalization(repository)
    second = plan_repository_normalization(first.repository)

    assert first.changed is True
    assert second.changed is False
    assert second.renames == {}
    assert second.reparented == ()
