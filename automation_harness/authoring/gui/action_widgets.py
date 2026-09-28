"""Shared GTK editors for canonical object-action inputs."""
from __future__ import annotations

import json
from typing import Any, Mapping

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.action_catalog import ActionInput, actions_for
from automation_harness.models.component import ComponentDefinition
from automation_harness.core.menu_navigation import menu_item_label, resolve_navigation


def interaction_actions(definition: ComponentDefinition):
    return tuple(
        action for action in actions_for(definition)
        if action.executor_step == "gui.object.action"
    )


def create_action_input_widget(
    item: ActionInput,
    component: ComponentDefinition,
    current: Any = None,
):
    if item.value_type == "menu_path":
        widget = MenuPathPicker(component.subobjects)
        selected = current if current is not None else item.default
        widget.set_selected(selected)
        return widget

    if item.value_type == "boolean":
        widget = Gtk.CheckButton()
        value = current if current is not None else item.default
        widget.set_active(bool(value))
        return widget

    if item.value_type == "number":
        widget = Gtk.SpinButton.new_with_range(-1000000000000.0, 1000000000000.0, 1.0)
        widget.set_digits(6)
        value = current if current is not None else item.default
        if value is not None:
            widget.set_value(float(value))
        return widget

    if item.value_type == "enum":
        widget = Gtk.ComboBoxText()
        for choice in item.choices:
            encoded = json.dumps(choice, default=str, separators=(",", ":"))
            widget.append(encoded, str(choice))
        selected = current if current is not None else item.default
        if selected is not None:
            _set_combo_active_id(
                widget, json.dumps(selected, default=str, separators=(",", ":")),
            )
        elif item.choices:
            widget.set_active(0)
        return widget

    if item.value_type == "object":
        options = object_selector_options(component.subobjects)
        if options:
            widget = Gtk.ComboBoxText()
            selected = current if current is not None else item.default
            selected_id = None
            for selector, label in options:
                encoded = json.dumps(selector, separators=(",", ":"), sort_keys=True)
                widget.append(encoded, label)
                if isinstance(selected, Mapping) and dict(selected) == selector:
                    selected_id = encoded
            if selected_id is not None:
                _set_combo_active_id(widget, selected_id)
            else:
                widget.set_active(0)
            return widget

    widget = Gtk.Entry()
    value = current if current is not None else item.default
    if value is not None:
        widget.set_text(_editable(value))
    widget.set_placeholder_text(item.description or item.value_type)
    return widget


def read_action_input(item: ActionInput, widget):
    if item.value_type == "menu_path":
        if isinstance(widget, MenuPathPicker):
            value = widget.selected_value()
            if value is None:
                return False, None
            path, enabled = value
            if not enabled:
                raise ValueError("the selected menu option is disabled")
            return True, path
        model = widget.get_model()
        iterator = widget.get_active_iter()
        if model is None or iterator is None:
            return False, None
        raw = model.get_value(iterator, 0)
        if not bool(model.get_value(iterator, 2)):
            raise ValueError("the selected menu option is disabled")
        if not raw:
            return False, None
        value = json.loads(raw)
        if isinstance(value, Mapping):
            path = value.get("path")
            if isinstance(path, list) and path and all(
                isinstance(segment, str) and segment for segment in path
            ):
                return True, path
        if isinstance(value, list):
            return True, value
        raise ValueError("menu picker produced an invalid navigation value")

    if item.value_type == "boolean":
        return True, bool(widget.get_active())

    if item.value_type == "number":
        value = widget.get_value()
        return True, int(value) if value.is_integer() else value

    if item.value_type == "enum":
        raw = widget.get_active_id()
        if not raw:
            return False, None
        return True, json.loads(raw)

    if item.value_type == "object" and isinstance(widget, Gtk.ComboBox):
        raw = widget.get_active_id()
        if not raw:
            return False, None
        return True, json.loads(raw)

    raw = widget.get_text().strip()
    if not raw:
        if item.default is not None:
            return True, item.default
        return False, None
    if item.value_type == "string":
        return True, raw
    try:
        return True, json.loads(raw)
    except ValueError:
        return True, raw


def menu_path_options(subobjects: Mapping[str, Mapping[str, Any]], prefix=(), labels=()):
    """Return selectable terminal paths with user-facing breadcrumb labels."""
    result = []
    for key, raw in subobjects.items():
        if not isinstance(raw, Mapping):
            continue
        path = prefix + (str(key),)
        label = menu_item_label(str(key), raw)
        label_path = labels + (label,)
        nested = raw.get("subobjects")
        kind = str(raw.get("kind") or "menu_item").replace(" ", "_").casefold()
        if isinstance(nested, Mapping) and nested:
            result.extend(menu_path_options(nested, path, label_path))
            continue
        if kind == "separator" or raw.get("selectable") is False:
            continue
        result.append((list(path), " > ".join(label_path)))
    return result


class MenuPathPicker(Gtk.Box):
    """Hierarchical menu picker preserving stable subobject paths.

    The tree is intentionally an authoring view over inventory metadata. It
    never resolves or clicks the live application. Disabled terminal entries
    stay visible for context but cannot be committed as action input.
    """

    _LABEL = 0
    _PATH = 1
    _ENABLED = 2
    _SELECTABLE = 3

    def __init__(self, subobjects: Mapping[str, Mapping[str, Any]]):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._subobjects = subobjects
        self._selected_path = None
        self._selected_enabled = False
        self._entry = Gtk.Entry()
        self._entry.set_hexpand(True)
        self._entry.set_placeholder_text("Route, e.g. File > Open")
        self.pack_start(self._entry, True, True, 0)
        self._button = Gtk.Button(label="Choose menu option…")
        self._button.set_halign(Gtk.Align.START)
        self.pack_start(self._button, False, False, 0)

        self._store = Gtk.TreeStore(str, str, bool, bool)
        self._tree = Gtk.TreeView(model=self._store)
        self._tree.set_headers_visible(False)
        renderer = Gtk.CellRendererText()
        column = Gtk.TreeViewColumn("Menu option", renderer, text=self._LABEL)
        column.set_cell_data_func(renderer, self._style_row)
        self._tree.append_column(column)
        self._tree.connect("row-activated", self._row_activated)

        self._popover = Gtk.Popover.new(self._button)
        self._popover.set_border_width(6)
        self._popover.set_size_request(360, 260)
        self._popover.add(self._tree)
        self._button.connect("clicked", lambda *_args: self._popover.popup())
        self._populate(subobjects)

    def _populate(self, subobjects, parent=None, prefix=(), labels=()):
        for key, raw in subobjects.items():
            if not isinstance(raw, Mapping):
                continue
            path = prefix + (str(key),)
            label_path = labels + (menu_item_label(str(key), raw),)
            nested = raw.get("subobjects")
            has_children = isinstance(nested, Mapping) and bool(nested)
            kind = str(raw.get("kind") or "menu_item").replace(" ", "_").casefold()
            if kind == "separator":
                continue
            enabled = raw.get("enabled") is not False
            selectable = not has_children and raw.get("selectable") is not False
            iterator = self._store.append(
                parent,
                (" > ".join(label_path), json.dumps(list(path)), enabled, selectable),
            )
            if has_children:
                self._populate(nested, iterator, path, label_path)
        if parent is None:
            self._tree.expand_all()

    def _style_row(self, _column, renderer, model, iterator):
        enabled = bool(model.get_value(iterator, self._ENABLED))
        selectable = bool(model.get_value(iterator, self._SELECTABLE))
        renderer.set_property("sensitive", enabled and selectable)
        renderer.set_property("style", "normal" if selectable else "italic")

    def _row_activated(self, _tree, path, _column):
        iterator = self._store.get_iter(path)
        if not bool(self._store.get_value(iterator, self._SELECTABLE)):
            return
        if not bool(self._store.get_value(iterator, self._ENABLED)):
            return
        self._selected_path = tuple(json.loads(self._store.get_value(iterator, self._PATH)))
        self._selected_enabled = True
        label = self._store.get_value(iterator, self._LABEL)
        self._entry.set_text(label)
        self._button.set_label("Choose from menu")
        self._popover.popdown()

    def set_selected(self, value):
        if value is None:
            return
        wanted = None
        if isinstance(value, (list, tuple)):
            wanted = tuple(str(item) for item in value)
        elif isinstance(value, str):
            try:
                wanted = tuple(resolve_navigation(self._as_mapping(), value))
            except ValueError:
                wanted = None
        if wanted is None:
            return
        iterator = self._store.get_iter_first()
        found = self._find_path(iterator, wanted)
        if found is not None:
            self._selected_path = wanted
            self._selected_enabled = bool(self._store.get_value(found, self._ENABLED))
            self._entry.set_text(self._store.get_value(found, self._LABEL))

    def selected_value(self):
        if self._selected_path is not None:
            return list(self._selected_path), self._selected_enabled
        route = self._entry.get_text().strip()
        if not route:
            return None
        path = resolve_navigation(self._as_mapping(), route)
        return path, True

    def _find_path(self, iterator, wanted):
        while iterator is not None:
            if tuple(json.loads(self._store.get_value(iterator, self._PATH))) == wanted:
                return iterator
            child = self._store.iter_children(iterator)
            if child is not None:
                found = self._find_path(child, wanted)
                if found is not None:
                    return found
            iterator = self._store.iter_next(iterator)
        return None

    def _as_mapping(self):
        # Only used for resolving a legacy breadcrumb string during initial
        # selection. The tree's stable path column remains authoritative after
        # construction.
        return self._subobjects


def object_selector_options(subobjects: Mapping[str, Mapping[str, Any]], prefix=(), labels=()):
    """Return structured selectors for logical children when available."""
    result = []
    for key, raw in subobjects.items():
        if not isinstance(raw, Mapping):
            continue
        path = prefix + (str(key),)
        label_path = labels + (menu_item_label(str(key), raw),)
        selector = raw.get("selector")
        if not isinstance(selector, Mapping):
            selector = {"kind": str(raw.get("kind") or "object"), "criteria": dict(raw.get("criteria") or {})}
        if raw.get("enabled") is not False and raw.get("selectable") is not False:
            result.append((dict(selector), " > ".join(label_path)))
        nested = raw.get("subobjects")
        if isinstance(nested, Mapping):
            result.extend(object_selector_options(nested, path, label_path))
    return result


def _set_combo_active_id(widget, active_id):
    if isinstance(widget, Gtk.ComboBoxText):
        widget.set_active_id(active_id)
        return
    model = widget.get_model()
    if model is None:
        return
    iterator = model.get_iter_first()
    while iterator is not None:
        if model.get_value(iterator, 0) == active_id:
            widget.set_active_iter(iterator)
            return
        iterator = model.iter_next(iterator)


def _editable(value):
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)
