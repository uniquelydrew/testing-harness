"""Object-scoped authoring actions.

The execution registry remains an internal adapter mechanism.  This module is
the user-facing vocabulary: actions are offered only when they apply to the
selected repository object and are converted into executable plan calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from automation_harness.models.component import ComponentDefinition
from automation_harness.models.gui import ActionType
from automation_harness.models.plan import StepCall


@dataclass(frozen=True)
class ActionInput:
    name: str
    value_type: str
    required: bool = True
    default: Any = None
    description: str = ""


@dataclass(frozen=True)
class ActionDefinition:
    action_id: str
    name: str
    description: str
    executor_step: str
    semantic_action: ActionType | None = None
    inputs: tuple[ActionInput, ...] = ()
    defaults: Mapping[str, Any] = field(default_factory=dict)
    category: str = "Interaction"

    def to_step_call(
        self,
        node_id: str,
        component_id: str,
        values: Mapping[str, Any] | None = None,
    ) -> StepCall:
        supplied = dict(self.defaults)
        supplied.update(values or {})
        if self.executor_step == "gui.object.action":
            supplied = {
                "component_id": component_id,
                "action": {
                    "type": self.semantic_action.value,
                    **supplied,
                },
            }
        else:
            supplied = {"component_id": component_id, **supplied}
        return StepCall(node_id=node_id, step_id=self.executor_step, inputs=supplied)


def _interaction(action: ActionType, name: str, description: str, *inputs: ActionInput) -> ActionDefinition:
    return ActionDefinition(action.value, name, description, "gui.object.action", action, tuple(inputs))


_VALUE = ActionInput("value", "any", description="Value supplied to the object action.")
_SELECTOR = ActionInput("selector", "object", description="Logical child/item selector.")
_MENU_PATH = ActionInput("path", "menu_path", description="Nested menu path from the captured menu hierarchy.")

INTERACTIONS: dict[ActionType, ActionDefinition] = {
    ActionType.CLICK: _interaction(ActionType.CLICK, "Click", "Click the selected object."),
    ActionType.ACTIVATE: _interaction(ActionType.ACTIVATE, "Activate", "Invoke the selected object's default accessible action."),
    ActionType.SET_TEXT: _interaction(ActionType.SET_TEXT, "Set Text", "Replace the selected object's text.", _VALUE),
    ActionType.CLEAR_TEXT: _interaction(ActionType.CLEAR_TEXT, "Clear Text", "Clear the selected object's text."),
    ActionType.APPEND_TEXT: _interaction(ActionType.APPEND_TEXT, "Append Text", "Append text to the selected object.", _VALUE),
    ActionType.SELECT: _interaction(ActionType.SELECT, "Select", "Select one logical child item.", _SELECTOR),
    ActionType.SELECT_ITEM: _interaction(ActionType.SELECT_ITEM, "Select Item", "Select one logical child item.", _SELECTOR),
    ActionType.SELECT_ROW: _interaction(ActionType.SELECT_ROW, "Select Row", "Select a table row.", _SELECTOR),
    ActionType.SELECT_CELL: _interaction(ActionType.SELECT_CELL, "Select Cell", "Select a table cell.", _SELECTOR),
    ActionType.SELECT_MENU_ITEM: _interaction(
        ActionType.SELECT_MENU_ITEM, "Select Menu Item",
        "Open a nested menu path and activate its terminal item as one uninterrupted operation.", _MENU_PATH,
    ),
    ActionType.SET_VALUE: _interaction(ActionType.SET_VALUE, "Set Value", "Set the selected object's numeric value.", _VALUE),
}


OBSERVATIONS = (
    ActionDefinition(
        "wait_for_state", "Wait for State", "Wait until an object state or property reaches an expected value.",
        "component.state.wait", inputs=(
            ActionInput("state_name", "string", description="State/property name."),
            ActionInput("expected", "any", description="Expected value."),
            ActionInput("timeout", "number", required=False, default=5.0),
            ActionInput("interval", "number", required=False, default=0.1),
        ), defaults={"timeout": 5.0, "interval": 0.1}, category="Synchronization",
    ),
    ActionDefinition(
        "assert_state", "Assert State", "Assert an object state or property.",
        "gui.object.state.assert", inputs=(
            ActionInput("state_name", "string", description="State/property name."),
            ActionInput("expected", "any", description="Expected value."),
        ), category="Assertion",
    ),
    ActionDefinition(
        "read_property", "Read Property", "Read a normalized object property into an optional test output.",
        "gui.object.property.get", inputs=(ActionInput("property_name", "string"),), category="Observation",
    ),
)


def actions_for(definition: ComponentDefinition) -> tuple[ActionDefinition, ...]:
    """Return stable, deterministic actions applicable to one captured object."""
    interactions = [INTERACTIONS[action] for action in sorted(definition.semantic_actions, key=lambda item: item.value) if action in INTERACTIONS]
    return tuple(interactions) + OBSERVATIONS


def action_by_id(definition: ComponentDefinition, action_id: str) -> ActionDefinition:
    for action in actions_for(definition):
        if action.action_id == action_id:
            return action
    raise ValueError("action %r is not available for object %r" % (action_id, definition.component_id))
