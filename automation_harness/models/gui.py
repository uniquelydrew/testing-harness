"""Framework-neutral GUI object vocabulary.

This module deliberately contains no Swing, JavaFX, AT-SPI, or Java Access
Bridge imports.  Adapters translate their native objects into these values.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol, runtime_checkable


class ObjectType(str, Enum):
    WINDOW = "window"; DIALOG = "dialog"; PANEL = "panel"; TAB_CONTAINER = "tab_container"; TAB = "tab"; SPLIT_CONTAINER = "split_container"; SCROLL_CONTAINER = "scroll_container"; TOOLBAR = "toolbar"
    LABEL = "label"; BUTTON = "button"; TOGGLE_BUTTON = "toggle_button"; CHECK_BOX = "check_box"; RADIO_BUTTON = "radio_button"; HYPERLINK = "hyperlink"
    TEXT_FIELD = "text_field"; PASSWORD_FIELD = "password_field"; TEXT_AREA = "text_area"; COMBO_BOX = "combo_box"; LIST = "list"; TREE = "tree"; TABLE = "table"; TREE_TABLE = "tree_table"
    MENU_BAR = "menu_bar"; MENU = "menu"; MENU_ITEM = "menu_item"; CHECK_MENU_ITEM = "check_menu_item"; RADIO_MENU_ITEM = "radio_menu_item"; CONTEXT_MENU = "context_menu"
    SLIDER = "slider"; SPINNER = "spinner"; PROGRESS_INDICATOR = "progress_indicator"; DATE_PICKER = "date_picker"; COLOR_PICKER = "color_picker"; IMAGE = "image"; GRAPHIC = "graphic"; CANVAS = "canvas"; CUSTOM = "custom"


class ActionType(str, Enum):
    CLICK = "click"; DOUBLE_CLICK = "double_click"; RIGHT_CLICK = "right_click"; FOCUS = "focus"
    SET_TEXT = "set_text"; CLEAR_TEXT = "clear_text"; APPEND_TEXT = "append_text"
    SELECT = "select"; DESELECT = "deselect"; SELECT_ITEM = "select_item"; SELECT_ITEMS = "select_items"; SELECT_ROW = "select_row"; SELECT_ROWS = "select_rows"; SELECT_CELL = "select_cell"; SELECT_MENU_ITEM = "select_menu_item"; TOGGLE = "toggle"
    OPEN = "open"; CLOSE = "close"; EXPAND = "expand"; COLLAPSE = "collapse"; SCROLL = "scroll"; SCROLL_TO = "scroll_to"
    EDIT = "edit"; COMMIT_EDIT = "commit_edit"; CANCEL_EDIT = "cancel_edit"; INCREMENT = "increment"; DECREMENT = "decrement"; SET_VALUE = "set_value"; DRAG = "drag"; DROP = "drop"; SHOW_CONTEXT_MENU = "show_context_menu"; ACTIVATE = "activate"; SUBMIT = "submit"


@dataclass(frozen=True)
class ObjectIdentity:
    repository_id: str
    name: str | None = None
    framework_id: str | None = None
    accessible_name: str | None = None


@dataclass(frozen=True)
class GuiBounds:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class PropertyKey:
    name: str
    value_type: type = object


class GuiProperties:
    TEXT = PropertyKey("text", str); VALUE = PropertyKey("value", object)
    VISIBLE = PropertyKey("visible", bool); ENABLED = PropertyKey("enabled", bool); FOCUSED = PropertyKey("focused", bool); SELECTED = PropertyKey("selected", bool); EDITABLE = PropertyKey("editable", bool); OPEN = PropertyKey("open", bool); EXPANDED = PropertyKey("expanded", bool)
    ITEM_COUNT = PropertyKey("item_count", int); ROW_COUNT = PropertyKey("row_count", int); COLUMN_COUNT = PropertyKey("column_count", int)
    TOOLTIP = PropertyKey("tooltip", str); ACCESSIBLE_NAME = PropertyKey("accessible_name", str); ACCESSIBLE_DESCRIPTION = PropertyKey("accessible_description", str)


@dataclass(frozen=True)
class GuiState:
    exists: bool
    visible: bool | None = None
    enabled: bool | None = None
    focused: bool | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class GuiObject(Protocol):
    """Framework-independent surface implemented by resolved object handles."""
    @property
    def identity(self) -> ObjectIdentity: ...
    @property
    def object_type(self) -> ObjectType: ...
    def gui_state(self) -> GuiState: ...
    def supports(self, action: ActionType | str) -> bool: ...
    def property(self, name: str) -> Any: ...
    def execute(self, action: "GuiAction | ActionType | str | dict", *, strategy: str | None = None) -> "ExecutionResult": ...


@dataclass(frozen=True)
class ObjectSelector:
    """Portable locator for virtual/logical children of a semantic object."""
    kind: str
    criteria: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "criteria": dict(self.criteria)}

    @classmethod
    def from_value(cls, value: "ObjectSelector | Mapping[str, Any] | None") -> "ObjectSelector | None":
        if value is None or isinstance(value, cls):
            return value
        if not isinstance(value, Mapping) or not isinstance(value.get("kind"), str):
            raise ValueError("selector must be an object with string kind")
        criteria = value.get("criteria", {})
        if not isinstance(criteria, Mapping):
            raise ValueError("selector.criteria must be a mapping")
        return cls(value["kind"], dict(criteria))


@dataclass(frozen=True)
class GuiAction:
    type: ActionType
    value: Any = None
    selector: ObjectSelector | None = None
    options: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_value(cls, value: "GuiAction | ActionType | str | Mapping[str, Any]") -> "GuiAction":
        if isinstance(value, cls): return value
        if isinstance(value, (ActionType, str)): return cls(ActionType(value))
        if not isinstance(value, Mapping): raise ValueError("action must be a string or mapping")
        raw_type = value.get("type")
        if raw_type is None: raise ValueError("action.type is required")
        known = {"type", "value", "selector", "options"}
        options = value.get("options", {})
        if not isinstance(options, Mapping): raise ValueError("action.options must be a mapping")
        return cls(ActionType(raw_type), value.get("value"), ObjectSelector.from_value(value.get("selector")), {**dict(options), **{k: v for k, v in value.items() if k not in known}})

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": self.type.value}
        if self.value is not None: result["value"] = self.value
        if self.selector is not None: result["selector"] = self.selector.to_dict()
        if self.options: result["options"] = dict(self.options)
        return result


@dataclass(frozen=True)
class ExecutionResult:
    action: ActionType
    strategy: str
    result: Mapping[str, Any] = field(default_factory=dict)
    attempts: tuple[Mapping[str, Any], ...] = ()


DEFAULT_ACTIONS: dict[ObjectType, frozenset[ActionType]] = {
    ObjectType.BUTTON: frozenset({ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK, ActionType.FOCUS, ActionType.SHOW_CONTEXT_MENU, ActionType.ACTIVATE}),
    ObjectType.TOGGLE_BUTTON: frozenset({ActionType.CLICK, ActionType.TOGGLE, ActionType.FOCUS}),
    ObjectType.CHECK_BOX: frozenset({ActionType.CLICK, ActionType.TOGGLE, ActionType.FOCUS}),
    ObjectType.RADIO_BUTTON: frozenset({ActionType.CLICK, ActionType.SELECT, ActionType.FOCUS}),
    ObjectType.TEXT_FIELD: frozenset({ActionType.CLICK, ActionType.FOCUS, ActionType.SET_TEXT, ActionType.CLEAR_TEXT, ActionType.APPEND_TEXT, ActionType.EDIT, ActionType.SHOW_CONTEXT_MENU}),
    ObjectType.PASSWORD_FIELD: frozenset({ActionType.CLICK, ActionType.FOCUS, ActionType.SET_TEXT, ActionType.CLEAR_TEXT, ActionType.APPEND_TEXT}),
    ObjectType.TEXT_AREA: frozenset({ActionType.CLICK, ActionType.FOCUS, ActionType.SET_TEXT, ActionType.CLEAR_TEXT, ActionType.APPEND_TEXT, ActionType.SCROLL}),
    ObjectType.COMBO_BOX: frozenset({ActionType.CLICK, ActionType.FOCUS, ActionType.OPEN, ActionType.CLOSE, ActionType.SELECT_ITEM}),
    ObjectType.LIST: frozenset({ActionType.CLICK, ActionType.FOCUS, ActionType.SELECT_ITEM, ActionType.SELECT_ITEMS, ActionType.SCROLL, ActionType.SCROLL_TO}),
    ObjectType.TREE: frozenset({ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.FOCUS, ActionType.SELECT_ITEM, ActionType.EXPAND, ActionType.COLLAPSE, ActionType.SCROLL, ActionType.SCROLL_TO}),
    ObjectType.TABLE: frozenset({ActionType.CLICK, ActionType.FOCUS, ActionType.SELECT_ROW, ActionType.SELECT_ROWS, ActionType.SELECT_CELL, ActionType.EDIT, ActionType.SCROLL, ActionType.SCROLL_TO}),
    ObjectType.MENU_BAR: frozenset({ActionType.SELECT_MENU_ITEM}),
    ObjectType.MENU: frozenset({ActionType.OPEN, ActionType.CLOSE, ActionType.CLICK, ActionType.SELECT_MENU_ITEM}),
    ObjectType.CONTEXT_MENU: frozenset({ActionType.SELECT_MENU_ITEM}),
    ObjectType.MENU_ITEM: frozenset({ActionType.CLICK, ActionType.ACTIVATE}),
    ObjectType.SLIDER: frozenset({ActionType.FOCUS, ActionType.SET_VALUE, ActionType.INCREMENT, ActionType.DECREMENT}),
    ObjectType.SPINNER: frozenset({ActionType.FOCUS, ActionType.SET_VALUE, ActionType.INCREMENT, ActionType.DECREMENT}),
}


PASSIVE_POINTER_TYPES = frozenset({ObjectType.LABEL, ObjectType.PROGRESS_INDICATOR})
NO_DEFAULT_CLICK_TYPES = PASSIVE_POINTER_TYPES | frozenset({ObjectType.CUSTOM})


def default_actions(object_type: ObjectType) -> frozenset[ActionType]:
    actions = DEFAULT_ACTIONS.get(object_type, frozenset())
    if object_type not in NO_DEFAULT_CLICK_TYPES:
        actions = actions | frozenset({ActionType.CLICK})
    return actions


def classify_accessibility(role: str | None, native_class: str | None = None) -> ObjectType:
    value = f"{role or ''} {native_class or ''}".casefold()
    rules = (("check menu item", ObjectType.CHECK_MENU_ITEM), ("radio menu item", ObjectType.RADIO_MENU_ITEM), ("checkbox", ObjectType.CHECK_BOX), ("check box", ObjectType.CHECK_BOX), ("radio", ObjectType.RADIO_BUTTON), ("toggle", ObjectType.TOGGLE_BUTTON), ("password", ObjectType.PASSWORD_FIELD), ("text area", ObjectType.TEXT_AREA), ("text", ObjectType.TEXT_FIELD), ("combo", ObjectType.COMBO_BOX), ("table", ObjectType.TABLE), ("tree", ObjectType.TREE), ("list", ObjectType.LIST), ("menu bar", ObjectType.MENU_BAR), ("context menu", ObjectType.CONTEXT_MENU), ("menu item", ObjectType.MENU_ITEM), ("menu", ObjectType.MENU), ("slider", ObjectType.SLIDER), ("spinner", ObjectType.SPINNER), ("progress", ObjectType.PROGRESS_INDICATOR), ("button", ObjectType.BUTTON), ("window", ObjectType.WINDOW), ("dialog", ObjectType.DIALOG), ("panel", ObjectType.PANEL), ("canvas", ObjectType.CANVAS), ("label", ObjectType.LABEL))
    return next((kind for needle, kind in rules if needle in value), ObjectType.CUSTOM)
