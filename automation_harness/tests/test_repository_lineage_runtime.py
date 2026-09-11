from automation_harness.authoring.repository_lineage_runtime import _lineage_rename_map
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import ComponentDefinition


def _repository(*names):
    return ComponentRepository({
        name: ComponentDefinition(component_id=name)
        for name in names
    })


def test_parent_lineage_rename_updates_all_descendants():
    repository = _repository(
        "Pane.CSSBridge.ContextMenu.Camera",
        "Pane.CSSBridge.ContextMenu.Tracking.Enable",
        "Pane.Other.Button",
    )
    result = _lineage_rename_map(repository, [
        ("Pane.CSSBridge", "CommandBridge", "branch"),
    ])
    assert result == {
        "Pane.CSSBridge.ContextMenu.Camera": "Pane.CommandBridge.ContextMenu.Camera",
        "Pane.CSSBridge.ContextMenu.Tracking.Enable": "Pane.CommandBridge.ContextMenu.Tracking.Enable",
    }


def test_nested_lineage_edits_compose_in_one_save():
    repository = _repository("Pane.CSSBridge.ContextMenu.Camera")
    result = _lineage_rename_map(repository, [
        ("Pane", "MainPane", "parent"),
        ("Pane.CSSBridge", "CommandBridge", "child"),
        ("Pane.CSSBridge.ContextMenu", "CameraMenu", "grandchild"),
    ])
    assert result == {
        "Pane.CSSBridge.ContextMenu.Camera": "MainPane.CommandBridge.CameraMenu.Camera",
    }


def test_lineage_rename_also_updates_object_at_same_qualified_path():
    repository = _repository("Pane.CSSBridge", "Pane.CSSBridge.Button")
    result = _lineage_rename_map(repository, [
        ("Pane.CSSBridge", "Bridge", "branch"),
    ])
    assert result == {
        "Pane.CSSBridge": "Pane.Bridge",
        "Pane.CSSBridge.Button": "Pane.Bridge.Button",
    }
