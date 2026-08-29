from __future__ import annotations

import json
import threading
from typing import Any, Mapping

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.authoring.capture_context import (
    CaptureContext,
    CaptureContextNode,
    build_capture_context,
    identity_descriptors,
    suggested_name,
)
from automation_harness.authoring.identity_editor import _known_classes


class ObjectIdentityWorkbench:
    """Compound capture authoring window.

    The workbench keeps capture scope, naming, identity evidence, highlighting,
    batch selection, and repository persistence in one place. The left side is
    a window-rooted semantic object tree; the right side is always the selected
    node's key=value identity/property view.
    """

    def __init__(self, app, captured):
        self.app = app
        self.original_capture = captured
        self.context = None
        self.nodes = {}
        self.names = {}
        self.identity_overrides = {}
        self.identity_fields = []
        self.ordinal_field = None
        self.selected_key = None
        self._loading = True

        self.window = Gtk.Window(title="Object Identity Workbench")
        self.window.set_transient_for(app.window)
        self.window.set_default_size(1180, 760)
        self.window.connect("destroy", self._on_destroy)
        self._build()
        self.window.show_all()
        self._set_status("Loading capture scope…")
        self._load_context_async()

    def _build(self):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(8)
        self.window.add(outer)

        toolbar = Gtk.Box(spacing=6)
        outer.pack_start(toolbar, False, False, 0)
        self._button(toolbar, "Highlight", self.highlight_selected)
        self._button(toolbar, "Check Siblings", self.check_siblings)
        self._button(toolbar, "Check Branch", self.check_branch)
        self._button(toolbar, "Clear Checks", self.clear_checks)
        self._button(toolbar, "Save Selected", self.save_selected)
        self._button(toolbar, "Save Checked", self.save_checked)
        self._button(toolbar, "Save Repository", self.save_repository)
        self.status = Gtk.Label(label="")
        self.status.set_halign(Gtk.Align.END)
        toolbar.pack_end(self.status, True, True, 0)

        pane = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        outer.pack_start(pane, True, True, 0)

        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        left.set_size_request(390, -1)
        pane.pack1(left, resize=True, shrink=False)
        scope_label = Gtk.Label(label="Capture Scope")
        scope_label.set_halign(Gtk.Align.START)
        left.pack_start(scope_label, False, False, 0)

        self.tree_store = Gtk.TreeStore(bool, str, str)
        self.tree = Gtk.TreeView(model=self.tree_store)
        toggle = Gtk.CellRendererToggle()
        toggle.connect("toggled", self._toggle_path)
        self.tree.append_column(Gtk.TreeViewColumn("Use", toggle, active=0))
        renderer = Gtk.CellRendererText()
        self.tree.append_column(Gtk.TreeViewColumn("Object", renderer, text=1))
        self.tree.get_selection().connect("changed", self._selection_changed)
        left.pack_start(self._scrolled(self.tree), True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pane.pack2(right, resize=True, shrink=False)

        name_row = Gtk.Box(spacing=8)
        right.pack_start(name_row, False, False, 0)
        name_row.pack_start(Gtk.Label(label="Name / Repository ID:"), False, False, 0)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_hexpand(True)
        self.name_entry.connect("changed", self._name_changed)
        name_row.pack_start(self.name_entry, True, True, 0)

        self.selection_caption = Gtk.Label(label="No object selected")
        self.selection_caption.set_halign(Gtk.Align.START)
        right.pack_start(self.selection_caption, False, False, 0)

        self.properties_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        right.pack_start(self._scrolled(self.properties_box), True, True, 0)

        pane.set_position(400)

    @staticmethod
    def _button(parent, label, callback):
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda *_args: callback())
        parent.pack_start(button, False, False, 0)
        return button

    @staticmethod
    def _scrolled(widget):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(widget)
        return scroll

    def _load_context_async(self):
        def worker():
            try:
                context = build_capture_context(self.original_capture)
            except Exception as exc:
                GLib.idle_add(self._context_failed, exc)
            else:
                GLib.idle_add(self._context_ready, context)

        threading.Thread(target=worker, name="capture-context-loader", daemon=True).start()

    def _context_ready(self, context):
        self.context = context
        self._loading = False
        self.tree_store.clear()
        self.nodes = {node.key: node for node in context.root.walk()}
        self._append_tree(None, context.root)
        self.tree.expand_all()
        self._select_key(context.target_key)
        target_iter = self._iter_for_key(context.target_key)
        if target_iter is not None:
            self.tree_store.set_value(target_iter, 0, True)
        self._set_status("Capture scope loaded")
        return False

    def _context_failed(self, error):
        self._loading = False
        self._set_status("Capture scope unavailable")
        self.app._error("Capture scope", "%s: %s" % (type(error).__name__, error))
        return False

    def _append_tree(self, parent_iter, node):
        iterator = self.tree_store.append(parent_iter, (bool(node.is_target), node.label, node.key))
        for child in node.children:
            self._append_tree(iterator, child)
        return iterator

    def _toggle_path(self, _renderer, path):
        iterator = self.tree_store.get_iter(path)
        current = bool(self.tree_store.get_value(iterator, 0))
        self.tree_store.set_value(iterator, 0, not current)

    def _selection_changed(self, selection):
        model, iterator = selection.get_selected()
        if iterator is None:
            return
        if self.selected_key is not None:
            self.names[self.selected_key] = self.name_entry.get_text().strip()
        key = model.get_value(iterator, 2)
        self.selected_key = key
        node = self.nodes.get(key)
        if node is None:
            return
        name = self.names.get(key)
        if not name:
            name = self._default_component_id(node)
            self.names[key] = name
        self.name_entry.set_text(name)
        self.selection_caption.set_text(self._selection_summary(node))
        self._render_properties(node)

    def _name_changed(self, entry):
        if self.selected_key is not None:
            self.names[self.selected_key] = entry.get_text().strip()

    def _selection_summary(self, node):
        kind = "Window root" if node.is_window_root else "Object"
        if node.is_target:
            kind += " · captured target"
        peer_count = len(self.context.selected_group(node.key)) if self.context else 0
        return "%s · %s peer(s) in semantic parent scope" % (kind, peer_count)

    def _render_properties(self, node):
        for child in self.properties_box.get_children():
            self.properties_box.remove(child)
        self.identity_fields = []
        self.ordinal_field = None

        try:
            captured = self._captured_for_node(node)
            strategy = captured.candidate_strategy()
            identity = self.identity_overrides.get(node.key)
            if identity is None:
                identity = strategy.options.get("identification") if strategy.type == "javafx" else captured.candidate_identification().to_dict()
            if not isinstance(identity, Mapping):
                identity = {"mandatory": {}}
        except Exception as exc:
            self._add_message("Identity unavailable: %s: %s" % (type(exc).__name__, exc))
            identity = {"mandatory": {}}

        inherited = self.context.inherited_descriptors(node.key) if self.context else {}
        common = self.context.common_peer_descriptors(node.key) if self.context else {}
        self._build_identity_section(identity, inherited, common, node)
        self._build_readonly_section("Inherited Tree Context", inherited, "Inherited from the selected object's window-rooted tree.")
        self._build_readonly_section("Common Sibling Evidence", common, "Shared by peers in the same semantic parent scope; useful for type/group context but not local discrimination.")
        self._build_runtime_section(node)
        self.properties_box.show_all()

    def _build_identity_section(self, identity, inherited, common, node):
        frame = Gtk.Frame(label="Identification")
        grid = Gtk.Grid()
        grid.set_border_width(8)
        grid.set_row_spacing(5)
        grid.set_column_spacing(8)
        frame.add(grid)
        self.properties_box.pack_start(frame, False, False, 0)

        known_classes = _known_classes("javafx" if self.context and self.context.framework == "javafx" else "", identity)
        row = 0
        for section in ("mandatory", "assistive"):
            values = identity.get(section, {})
            if not isinstance(values, Mapping):
                continue
            for path, value in _flatten(values):
                full_key = "%s.%s" % (section, _path_text(path))
                logical_key = _logical_leaf(path)
                source = _scope_for(logical_key, value, inherited, common)
                check = Gtk.CheckButton()
                check.set_active(True)
                grid.attach(check, 0, row, 1, 1)
                key_label = Gtk.Label(label=full_key)
                key_label.set_halign(Gtk.Align.START)
                grid.attach(key_label, 1, row, 1, 1)
                grid.attach(Gtk.Label(label="="), 2, row, 1, 1)
                field, entry = _value_field(logical_key, value, known_classes)
                field.set_hexpand(True)
                grid.attach(field, 3, row, 1, 1)
                source_label = Gtk.Label(label=source)
                source_label.set_halign(Gtk.Align.START)
                grid.attach(source_label, 4, row, 1, 1)
                if source in {"inherited", "common"}:
                    # Retain the condition in the effective identity for the
                    # current global resolver, but visually de-emphasize it as
                    # scope evidence rather than local discrimination.
                    key_label.set_sensitive(False)
                    field.set_sensitive(False)
                    source_label.set_sensitive(False)
                    check.set_sensitive(False)
                self.identity_fields.append((section, path, value, check, entry))
                row += 1

        ordinal = identity.get("ordinal")
        if isinstance(ordinal, int) and not isinstance(ordinal, bool) and ordinal >= 0:
            check = Gtk.CheckButton()
            check.set_active(True)
            grid.attach(check, 0, row, 1, 1)
            key_label = Gtk.Label(label="ordinal")
            key_label.set_halign(Gtk.Align.START)
            grid.attach(key_label, 1, row, 1, 1)
            grid.attach(Gtk.Label(label="="), 2, row, 1, 1)
            spin = Gtk.SpinButton.new_with_range(0, 100000, 1)
            spin.set_value(ordinal)
            grid.attach(spin, 3, row, 1, 1)
            source_label = Gtk.Label(label="final discriminator")
            source_label.set_halign(Gtk.Align.START)
            grid.attach(source_label, 4, row, 1, 1)
            self.ordinal_field = (check, spin)
            row += 1

        if row == 0:
            label = Gtk.Label(label="No durable identity conditions were inferred for this node.")
            label.set_halign(Gtk.Align.START)
            grid.attach(label, 0, 0, 5, 1)

    def _build_readonly_section(self, title, values, note):
        frame = Gtk.Frame(label=title)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_border_width(8)
        frame.add(box)
        note_label = Gtk.Label(label=note)
        note_label.set_halign(Gtk.Align.START)
        note_label.set_line_wrap(True)
        note_label.set_sensitive(False)
        box.pack_start(note_label, False, False, 0)
        if not values:
            empty = Gtk.Label(label="None")
            empty.set_halign(Gtk.Align.START)
            empty.set_sensitive(False)
            box.pack_start(empty, False, False, 0)
        else:
            for key, value in sorted(values.items()):
                label = Gtk.Label(label="%s = %s" % (key, _display(value)))
                label.set_halign(Gtk.Align.START)
                label.set_selectable(True)
                label.set_sensitive(False)
                box.pack_start(label, False, False, 0)
        self.properties_box.pack_start(frame, False, False, 0)

    def _build_runtime_section(self, node):
        frame = Gtk.Frame(label="Runtime Properties")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_border_width(8)
        frame.add(box)
        values = identity_descriptors(node.payload)
        for key in ("visible", "disabled", "focused", "managed", "focus_traversable", "sibling_index", "sibling_count", "bounds", "style_classes"):
            if key in node.payload and node.payload.get(key) is not None:
                values[key] = node.payload.get(key)
        for key, value in sorted(values.items()):
            label = Gtk.Label(label="%s = %s" % (key, _display(value)))
            label.set_halign(Gtk.Align.START)
            label.set_selectable(True)
            box.pack_start(label, False, False, 0)
        self.properties_box.pack_start(frame, False, False, 0)

    def _add_message(self, text):
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        label.set_line_wrap(True)
        self.properties_box.pack_start(label, False, False, 0)

    def current_identity(self):
        leaves = {"mandatory": {}, "assistive": {}}
        for section, path, original, check, entry in self.identity_fields:
            if not check.get_active():
                continue
            leaves[section][path] = _parse_value(
                entry.get_text().strip(),
                original,
                "%s.%s" % (section, _path_text(path)),
            )
        mandatory = _rebuild_from_paths(leaves["mandatory"])
        assistive = _rebuild_from_paths(leaves["assistive"])
        if not mandatory:
            raise ValueError("At least one mandatory key=value identity condition is required.")
        result = {"mandatory": mandatory}
        if assistive:
            result["assistive"] = assistive
        if self.ordinal_field is not None:
            enabled, spin = self.ordinal_field
            if enabled.get_active():
                result["ordinal"] = int(spin.get_value_as_int())
        return result

    def highlight_selected(self):
        node = self._selected_node()
        if node is None:
            return
        bounds = node.payload.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            return self.app._info("Highlight", "The selected object has no screen bounds.")
        rect = tuple(int(round(float(value))) for value in bounds)
        try:
            self.app._show_highlight(rect, False)
            GLib.timeout_add(1400, self._clear_highlight)
        except Exception as exc:
            self.app._error("Highlight failed", "%s: %s" % (type(exc).__name__, exc))

    def _clear_highlight(self):
        self.app._clear_highlight()
        return False

    def check_siblings(self):
        node = self._selected_node()
        if node is None or self.context is None:
            return
        parent = self.context.parent_of(node.key)
        if parent is None:
            self._set_checked(node.key, True)
            return
        for child in parent.children:
            self._set_checked(child.key, True)
        self._set_status("Checked semantic sibling group")

    def check_branch(self):
        node = self._selected_node()
        if node is None:
            return
        for item in node.walk():
            self._set_checked(item.key, True)
        self._set_status("Checked selected branch")

    def clear_checks(self):
        iterator = self.tree_store.get_iter_first()
        while iterator is not None:
            self._clear_iter(iterator)
            iterator = self.tree_store.iter_next(iterator)
        self._set_status("Cleared batch selection")

    def _clear_iter(self, iterator):
        self.tree_store.set_value(iterator, 0, False)
        child = self.tree_store.iter_children(iterator)
        while child is not None:
            self._clear_iter(child)
            child = self.tree_store.iter_next(child)

    def save_selected(self):
        node = self._selected_node()
        if node is None:
            return
        try:
            identity = self.current_identity()
            self.identity_overrides[node.key] = identity
            component_id = self.name_entry.get_text().strip()
            if not component_id:
                raise ValueError("Name / Repository ID is required.")
            self._save_node(node, component_id, identity)
            self._set_status("Saved %s to working repository" % component_id)
        except Exception as exc:
            self.app._error("Save object", "%s: %s" % (type(exc).__name__, exc))

    def save_checked(self):
        checked = self._checked_keys()
        if not checked:
            return self.app._info("Batch save", "Check one or more objects in the capture tree first.")
        saved = 0
        errors = []
        if self.selected_key is not None:
            try:
                self.identity_overrides[self.selected_key] = self.current_identity()
            except Exception:
                pass
        for key in checked:
            node = self.nodes.get(key)
            if node is None:
                continue
            try:
                captured = self._captured_for_node(node)
                identity = self.identity_overrides.get(key)
                if identity is None:
                    strategy = captured.candidate_strategy()
                    identity = strategy.options.get("identification") if strategy.type == "javafx" else captured.candidate_identification().to_dict()
                component_id = self.names.get(key) or self._default_component_id(node)
                self.names[key] = component_id
                self._save_node(node, component_id, identity)
                saved += 1
            except Exception as exc:
                errors.append("%s: %s" % (node.label, exc))
        if saved:
            self._set_status("Batch saved %d object(s) to working repository" % saved)
        if errors:
            self.app._error("Batch save", "Saved %d object(s).\n\n%s" % (saved, "\n".join(errors[:12])))

    def _save_node(self, node, component_id, identity):
        captured = self._captured_for_node(node)
        existing = self.app.repository.components.get(component_id)
        revision = 1 if existing is None else existing.revision + 1
        definition = self.app.capture.definition_from_capture(
            component_id,
            captured,
            identification=identity,
            revision=revision,
        )
        self.app.repository = self.app.repository.with_component(definition)
        if hasattr(self.app, "_mark_repository_dirty"):
            self.app._mark_repository_dirty(True)
        self.app.refresh_objects()

    def save_repository(self):
        if hasattr(self.app, "save_repository"):
            self.app.save_repository()

    def _captured_for_node(self, node):
        if node.key == self.context.target_key:
            return self.original_capture
        return self.context.captured_component(node.key)

    def _selected_node(self):
        if self.selected_key is None:
            return None
        return self.nodes.get(self.selected_key)

    def _default_component_id(self, node):
        base = suggested_name(node.payload)
        parent = self.context.parent_of(node.key) if self.context else None
        if parent is not None:
            peers = self.context.selected_group(node.key)
            duplicates = [peer for peer in peers if suggested_name(peer.payload) == base]
            if len(duplicates) > 1:
                layout = node.payload.get("layout")
                if isinstance(layout, Mapping) and ("grid_row" in layout or "grid_column" in layout):
                    return "%s_%s_%s" % (base, layout.get("grid_row", 0), layout.get("grid_column", 0))
                index = node.payload.get("sibling_index")
                if index is not None:
                    return "%s_%s" % (base, index)
        return base

    def _checked_keys(self):
        result = []
        iterator = self.tree_store.get_iter_first()
        while iterator is not None:
            self._collect_checked(iterator, result)
            iterator = self.tree_store.iter_next(iterator)
        return result

    def _collect_checked(self, iterator, result):
        if self.tree_store.get_value(iterator, 0):
            result.append(self.tree_store.get_value(iterator, 2))
        child = self.tree_store.iter_children(iterator)
        while child is not None:
            self._collect_checked(child, result)
            child = self.tree_store.iter_next(child)

    def _set_checked(self, key, value):
        iterator = self._iter_for_key(key)
        if iterator is not None:
            self.tree_store.set_value(iterator, 0, bool(value))

    def _iter_for_key(self, key):
        iterator = self.tree_store.get_iter_first()
        while iterator is not None:
            found = self._find_iter(iterator, key)
            if found is not None:
                return found
            iterator = self.tree_store.iter_next(iterator)
        return None

    def _find_iter(self, iterator, key):
        if self.tree_store.get_value(iterator, 2) == key:
            return iterator
        child = self.tree_store.iter_children(iterator)
        while child is not None:
            found = self._find_iter(child, key)
            if found is not None:
                return found
            child = self.tree_store.iter_next(child)
        return None

    def _select_key(self, key):
        iterator = self._iter_for_key(key)
        if iterator is None:
            return
        self.tree.get_selection().select_iter(iterator)
        self.tree.scroll_to_cell(self.tree_store.get_path(iterator))

    def _set_status(self, value):
        self.status.set_text(value)
        try:
            self.app._set_status(value)
        except Exception:
            pass

    def _on_destroy(self, *_args):
        try:
            if getattr(self.app, "_capture_workbench", None) is self:
                self.app._capture_workbench = None
        except Exception:
            pass


def open_capture_workbench(app, captured):
    existing = getattr(app, "_capture_workbench", None)
    if existing is not None:
        try:
            existing.window.destroy()
        except Exception:
            pass
    workbench = ObjectIdentityWorkbench(app, captured)
    app._capture_workbench = workbench
    return workbench


def _scope_for(logical_key, value, inherited, common):
    if logical_key in common and common[logical_key] == value:
        return "common"
    if logical_key in inherited and inherited[logical_key] == value:
        return "inherited"
    if logical_key == "window" and inherited.get("window") == value:
        return "inherited"
    return "local"


def _value_field(logical_key, value, known_classes):
    if logical_key.endswith("class") or logical_key == "class":
        combo = Gtk.ComboBoxText.new_with_entry()
        for item in known_classes:
            combo.append_text(item)
        entry = combo.get_child()
        entry.set_text("" if value is None else str(value))
        return combo, entry
    entry = Gtk.Entry()
    if isinstance(value, bool):
        entry.set_text("true" if value else "false")
    elif value is None:
        entry.set_text("")
    else:
        entry.set_text(str(value))
    return entry, entry


def _flatten(value, path=()):
    if isinstance(value, Mapping):
        for key, child in value.items():
            for item in _flatten(child, path + (str(key),)):
                yield item
        return
    if isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            for item in _flatten(child, path + (index,)):
                yield item
        return
    yield path, value


def _path_text(path):
    text = ""
    for token in path:
        if isinstance(token, int):
            text += "[%d]" % token
        else:
            text += ("." if text else "") + str(token)
    return text


def _logical_leaf(path):
    if not path:
        return ""
    root = str(path[0])
    if root in {"layout", "properties", "parent"} and len(path) >= 2:
        return "%s.%s" % (root, path[-1])
    return str(path[-1])


def _parse_value(text, original, label):
    if isinstance(original, bool):
        folded = text.casefold()
        if folded not in {"true", "false"}:
            raise ValueError("%s must be true or false" % label)
        return folded == "true"
    if isinstance(original, int) and not isinstance(original, bool):
        try:
            return int(text)
        except ValueError:
            raise ValueError("%s must be an integer" % label)
    if isinstance(original, float):
        try:
            return float(text)
        except ValueError:
            raise ValueError("%s must be numeric" % label)
    if original is None:
        return text
    if not text and isinstance(original, str):
        raise ValueError("%s requires a non-empty value" % label)
    return text


def _rebuild_from_paths(leaves):
    root = {}
    for path, value in leaves.items():
        if not path:
            continue
        current = root
        for index, token in enumerate(path):
            last = index == len(path) - 1
            next_token = None if last else path[index + 1]
            if isinstance(token, int):
                raise ValueError("identity cannot begin with an array index")
            if last:
                current[token] = value
                continue
            if isinstance(next_token, int):
                values = current.setdefault(token, [])
                while len(values) <= next_token:
                    values.append({})
                if index + 1 == len(path) - 1:
                    values[next_token] = value
                    break
                child = values[next_token]
                if not isinstance(child, dict):
                    child = {}
                    values[next_token] = child
                remaining = path[index + 2:]
                if remaining:
                    _assign_nested(child, remaining, value)
                break
            current = current.setdefault(token, {})
    return root


def _assign_nested(root, path, value):
    current = root
    for index, token in enumerate(path):
        last = index == len(path) - 1
        if isinstance(token, int):
            raise ValueError("nested array identity editing is unsupported at this level")
        if last:
            current[token] = value
        else:
            current = current.setdefault(token, {})


def _display(value):
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)
