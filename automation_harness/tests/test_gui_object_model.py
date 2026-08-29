from __future__ import annotations

import pytest

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.gui_execution import ExecutionStrategyResolver
from automation_harness.models.component import ComponentDefinition
from automation_harness.models.gui import ActionType, ExecutionResult, GuiAction, ObjectSelector, ObjectType, classify_accessibility, default_actions


def test_taxonomy_and_default_profiles_are_semantic_not_framework_specific():
    assert classify_accessibility("push button", "javax.swing.JButton") is ObjectType.BUTTON
    assert classify_accessibility("table", "javafx.scene.control.TableView") is ObjectType.TABLE
    assert ActionType.CLICK in default_actions(ObjectType.BUTTON)
    assert ActionType.SET_TEXT in default_actions(ObjectType.TEXT_FIELD)


def test_v1_repository_remains_usable_as_semantic_click_object():
    repository = ComponentRepository.from_document({"version": 1, "components": {"save": {
        "actions": ["resolve", "activate"], "strategies": [{"type": "atspi", "name": "Save"}],
    }}})
    definition = repository.get("save")
    assert definition.object_type is ObjectType.CUSTOM
    assert definition.supports(ActionType.CLICK)
    assert repository.get(definition.object_id) is definition


def test_v2_repository_migrates_to_v3_and_persists_immutable_identity():
    repository = ComponentRepository.from_document({"version": 2, "components": {"orders": {
        "object_type": "table", "actions": ["select_row", "select_cell"],
        "properties": {"row_count": 5}, "framework": "javafx", "native_class": "javafx.scene.control.TableView",
        "subobjects": {"first_row": {"kind": "table_row", "criteria": {"index": 0}}},
        "strategies": [{"type": "java_accessibility", "identification": {"mandatory": {"name": "Orders"}}}],
    }}})
    definition = repository.get("orders")
    assert definition.supports(ActionType.SELECT_CELL)
    assert definition.subobjects["first_row"]["kind"] == "table_row"
    document = repository.to_document()
    assert document["version"] == 3
    assert document["components"]["orders"]["object_type"] == "table"
    assert document["components"]["orders"]["object_id"] == definition.object_id
    assert ComponentRepository.from_document(document).get(definition.object_id).component_id == "orders"


def test_immutable_object_id_survives_logical_rename():
    repository = ComponentRepository.from_document({"version": 2, "components": {"save": {
        "object_type": "button", "actions": ["click"],
        "strategies": [{"type": "atspi", "name": "Save", "role": "push button"}],
    }}})
    object_id = repository.get("save").object_id

    renamed = repository.rename("save", "submit_credentials")

    assert not renamed.contains("save")
    assert renamed.get(object_id).component_id == "submit_credentials"
    assert renamed.get("submit_credentials").object_id == object_id
    reloaded = ComponentRepository.from_document(renamed.to_document())
    assert reloaded.get(object_id).component_id == "submit_credentials"


def test_component_revision_cannot_replace_existing_object_identity():
    original = ComponentDefinition(component_id="save")
    replacement = ComponentDefinition(component_id="save")
    assert original.object_id != replacement.object_id

    repository = ComponentRepository({"save": original}).with_component(replacement)

    assert repository.get("save").object_id == original.object_id


def test_repository_rejects_duplicate_immutable_object_ids():
    object_id = "2b692073-a062-4d25-9c90-23e41c2a96da"
    with pytest.raises(ComponentRepositoryError, match="assigned to both"):
        ComponentRepository.from_document({"version": 3, "components": {
            "first": {
                "object_id": object_id,
                "actions": ["click"],
                "strategies": [{"type": "atspi", "identification": {"mandatory": {"name": "First"}}}],
            },
            "second": {
                "object_id": object_id,
                "actions": ["click"],
                "strategies": [{"type": "atspi", "identification": {"mandatory": {"name": "Second"}}}],
            },
        }})


def test_v2_actions_do_not_need_legacy_resolve_marker():
    repository = ComponentRepository.from_document({"version": 2, "components": {"x": {
        "object_type": "button", "actions": ["click"], "strategies": [{"type": "atspi", "name": "X"}],
    }}})
    assert repository.get("x").supports(ActionType.CLICK)


def test_invalid_semantic_type_is_rejected():
    with pytest.raises(ComponentRepositoryError, match="semantic type"):
        ComponentRepository.from_document({"version": 2, "components": {"x": {
            "object_type": "not-a-control", "actions": ["click"], "strategies": [{"type": "atspi", "name": "X"}],
        }}})


def test_action_and_selector_round_trip():
    action = GuiAction.from_value({"type": "select_cell", "selector": {"kind": "table_cell", "criteria": {"row": 2, "column": "status"}}})
    assert action.selector == ObjectSelector("table_cell", {"row": 2, "column": "status"})
    assert GuiAction.from_value(action.to_dict()) == action


def test_strategy_resolver_uses_requested_executor_and_collects_prior_failures():
    class Failing:
        name = "accessibility"
        def supports(self, target, action): return True
        def execute(self, target, action): raise RuntimeError("not exposed")
    class Working:
        name = "java_agent"
        def supports(self, target, action): return True
        def execute(self, target, action): return ExecutionResult(action.type, self.name, {"ok": True})

    action = GuiAction(ActionType.CLICK)
    result = ExecutionStrategyResolver([Failing(), Working()]).execute(object(), action)
    assert result.strategy == "java_agent"
    assert result.attempts[0]["strategy"] == "accessibility"
    assert ExecutionStrategyResolver([Working()]).execute(object(), action, "java_agent").result == {"ok": True}
