"""Canonical semantic interaction mechanics.

Object types are the author-facing vocabulary.  Families are deliberately a
second axis: they describe how capture, recording, and execution should treat
native control implementations without flattening their semantic identity.
"""
from __future__ import annotations

from enum import Enum

from automation_harness.models.gui import ObjectType


class InteractionFamily(str, Enum):
    POPUP_SELECTOR = "popup_selector"
    MENU_OWNER = "menu_owner"
    SELECTABLE = "selectable"
    TEXT_INPUT = "text_input"
    VALUE_CONTROL = "value_control"
    COLLECTION_SELECTOR = "collection_selector"


_OBJECT_FAMILIES = {
    ObjectType.COMBO_BOX: InteractionFamily.POPUP_SELECTOR,
    ObjectType.DATE_PICKER: InteractionFamily.POPUP_SELECTOR,
    ObjectType.COLOR_PICKER: InteractionFamily.POPUP_SELECTOR,
    ObjectType.MENU_BAR: InteractionFamily.MENU_OWNER,
    ObjectType.MENU: InteractionFamily.MENU_OWNER,
    ObjectType.CONTEXT_MENU: InteractionFamily.MENU_OWNER,
    ObjectType.TOGGLE_BUTTON: InteractionFamily.SELECTABLE,
    ObjectType.CHECK_BOX: InteractionFamily.SELECTABLE,
    ObjectType.RADIO_BUTTON: InteractionFamily.SELECTABLE,
    ObjectType.TEXT_FIELD: InteractionFamily.TEXT_INPUT,
    ObjectType.PASSWORD_FIELD: InteractionFamily.TEXT_INPUT,
    ObjectType.TEXT_AREA: InteractionFamily.TEXT_INPUT,
    ObjectType.SLIDER: InteractionFamily.VALUE_CONTROL,
    ObjectType.SPINNER: InteractionFamily.VALUE_CONTROL,
    ObjectType.LIST: InteractionFamily.COLLECTION_SELECTOR,
    ObjectType.TREE: InteractionFamily.COLLECTION_SELECTOR,
    ObjectType.TABLE: InteractionFamily.COLLECTION_SELECTOR,
    ObjectType.TREE_TABLE: InteractionFamily.COLLECTION_SELECTOR,
    ObjectType.TAB_CONTAINER: InteractionFamily.COLLECTION_SELECTOR,
}

# JavaFX has controls whose public semantic identity is intentionally mapped
# to ObjectType.COMBO_BOX or MENU. Keep these native facts here rather than
# duplicating partially-overlapping checks in every recording consumer.
_JAVA_FX_NATIVE_FAMILIES = {
    "combobox": InteractionFamily.POPUP_SELECTOR,
    "choicebox": InteractionFamily.POPUP_SELECTOR,
    "datepicker": InteractionFamily.POPUP_SELECTOR,
    "colorpicker": InteractionFamily.POPUP_SELECTOR,
    "menubar": InteractionFamily.MENU_OWNER,
    "menubutton": InteractionFamily.MENU_OWNER,
    "splitmenubutton": InteractionFamily.MENU_OWNER,
    "menubarbutton": InteractionFamily.MENU_OWNER,
    "contextmenu": InteractionFamily.MENU_OWNER,
    "togglebutton": InteractionFamily.SELECTABLE,
    "checkbox": InteractionFamily.SELECTABLE,
    "radiobutton": InteractionFamily.SELECTABLE,
    "textfield": InteractionFamily.TEXT_INPUT,
    "passwordfield": InteractionFamily.TEXT_INPUT,
    "textarea": InteractionFamily.TEXT_INPUT,
    "slider": InteractionFamily.VALUE_CONTROL,
    "spinner": InteractionFamily.VALUE_CONTROL,
    "listview": InteractionFamily.COLLECTION_SELECTOR,
    "treeview": InteractionFamily.COLLECTION_SELECTOR,
    "tableview": InteractionFamily.COLLECTION_SELECTOR,
    "treetableview": InteractionFamily.COLLECTION_SELECTOR,
    "tabpane": InteractionFamily.COLLECTION_SELECTOR,
}


def interaction_family(object_type: ObjectType | None, native_class: str | None = None) -> InteractionFamily | None:
    """Classify a durable object without changing its semantic ObjectType."""
    simple = str(native_class or "").rsplit(".", 1)[-1].casefold()
    native = _JAVA_FX_NATIVE_FAMILIES.get(simple)
    return native or _OBJECT_FAMILIES.get(object_type)


def is_popup_selector(object_type: ObjectType | None, native_class: str | None = None) -> bool:
    return interaction_family(object_type, native_class) is InteractionFamily.POPUP_SELECTOR


def is_menu_owner(object_type: ObjectType | None, native_class: str | None = None) -> bool:
    return interaction_family(object_type, native_class) is InteractionFamily.MENU_OWNER
