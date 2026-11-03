"""Object-scoped authoring actions.

The execution registry remains an internal adapter mechanism.  This module is
the user-facing vocabulary: actions are offered only when they apply to the
selected repository object and are converted into executable plan calls.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from automation_harness.models.component import ComponentDefinition
from automation_harness.core.component_catalog import canonical_action_types
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.models.plan import StepCall


@dataclass(frozen=True)
class ActionInput:
    name: str
    value_type: str
    required: bool = True
    default: Any = None
    description: str = ""
    choices: tuple[Any, ...] = ()


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


_TEXT_VALUE = ActionInput(
    "value", "string", description="Text value supplied to the object action.",
)
_NUMERIC_VALUE = ActionInput(
    "value", "number", description="Numeric value supplied to the object action.",
)
_SELECTOR = ActionInput("selector", "object", description="Logical child/item selector.")
_MENU_PATH = ActionInput(
    "path", "menu_path",
    description="Menu route using captured names or IDs, for example File > Export > PDF.",
)

INTERACTIONS: dict[ActionType, ActionDefinition] = {
    ActionType.CLICK: _interaction(ActionType.CLICK, "Click", "Click the selected object."),
    ActionType.ACTIVATE: _interaction(ActionType.ACTIVATE, "Activate", "Invoke the selected object's default accessible action."),
    ActionType.FOCUS: _interaction(ActionType.FOCUS, "Focus", "Move keyboard focus to the selected object."),
    ActionType.CLOSE: _interaction(ActionType.CLOSE, "Close", "Request that the selected window or dialog close, respecting its close handlers."),
    ActionType.SET_TEXT: _interaction(ActionType.SET_TEXT, "Set Text", "Replace the selected object's text.", _TEXT_VALUE),
    ActionType.CLEAR_TEXT: _interaction(ActionType.CLEAR_TEXT, "Clear Text", "Clear the selected object's text."),
    ActionType.APPEND_TEXT: _interaction(ActionType.APPEND_TEXT, "Append Text", "Append text to the selected object.", _TEXT_VALUE),
    ActionType.SELECT: _interaction(ActionType.SELECT, "Select", "Select one logical child item.", _SELECTOR),
    ActionType.SELECT_ITEM: _interaction(ActionType.SELECT_ITEM, "Select Item", "Select one logical child item.", _SELECTOR),
    ActionType.SELECT_ROW: _interaction(ActionType.SELECT_ROW, "Select Row", "Select a table row.", _SELECTOR),
    ActionType.SELECT_CELL: _interaction(ActionType.SELECT_CELL, "Select Cell", "Select a table cell.", _SELECTOR),
    ActionType.SELECT_MENU_ITEM: _interaction(
        ActionType.SELECT_MENU_ITEM, "Select Menu Option",
        "Navigate the captured menu hierarchy and activate the terminal option as one uninterrupted operation.", _MENU_PATH,
    ),
    ActionType.SET_VALUE: _interaction(ActionType.SET_VALUE, "Set Value", "Set the selected object's numeric value.", _NUMERIC_VALUE),
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
        "assert_exists", "Assert Exists",
        "Assert that the object resolves and retain a component screenshot as actual evidence.",
        "gui.object.exists.assert", category="Assertion",
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
    """Return canonical user-facing actions applicable to one repository object."""
    allowed = canonical_action_types(definition.object_type)
    available = definition.semantic_actions
    interactions = [
        INTERACTIONS[action]
        for action in sorted(allowed & available, key=lambda item: item.value)
        if action in INTERACTIONS
    ]
    return tuple(interactions) + OBSERVATIONS


def tab_inventory_selector_options(definition: ComponentDefinition) -> tuple[tuple[dict[str, Any], str], ...]:
    """Return owner-scoped logical Tab selectors from legacy TabPane inventory.

    Older .ahobjects files retained individual tabs only in properties.tabs,
    not as logical subobjects. The authoring picker must still expose those
    known tabs without treating the transient tab-header skin as an object.
    Index corroborates the ID/text identity; it never identifies the owner.
    """
    if definition.object_type is not ObjectType.TAB_CONTAINER:
        return ()
    inventory = definition.properties.get("tabs", ())
    if not isinstance(inventory, (list, tuple)):
        return ()
    options = []
    for position, raw in enumerate(inventory):
        if not isinstance(raw, Mapping) or bool(raw.get("disabled", False)):
            continue
        tab_id = raw.get("id")
        tab_text = raw.get("text")
        if not isinstance(tab_id, str) or not tab_id.strip():
            tab_id = None
        if not isinstance(tab_text, str) or not tab_text.strip():
            tab_text = None
        if tab_id is None and tab_text is None:
            continue
        criteria = {}
        if tab_id is not None:
            criteria["id"] = tab_id
        if tab_text is not None:
            criteria["text"] = tab_text
        raw_index = raw.get("index")
        if isinstance(raw_index, int) and not isinstance(raw_index, bool) and raw_index >= 0:
            criteria["index"] = raw_index
        else:
            criteria["index"] = position
        options.append(({"kind": "tab", "criteria": criteria}, tab_text or tab_id))
    labels = [label for _selector, label in options]
    return tuple(
        (selector, "%s (tab %d)" % (label, selector["criteria"]["index"] + 1)
         if labels.count(label) > 1 else label)
        for selector, label in options
    )


def action_by_id(definition: ComponentDefinition, action_id: str) -> ActionDefinition:
    for action in actions_for(definition):
        if action.action_id == action_id:
            return action
    raise ValueError("action %r is not available for object %r" % (action_id, definition.component_id))
