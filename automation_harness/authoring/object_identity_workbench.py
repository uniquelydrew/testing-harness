from __future__ import annotations

import json
import threading
from typing import Any, Mapping

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.authoring.capture_context import (
    build_capture_context,
    build_recording_context,
    suggested_name,
)
from automation_harness.authoring.capture_property_policy import (
    available_properties,
    property_policy,
)
from automation_harness.authoring.identity_editor import _known_classes
from automation_harness.drivers.javafx_bridge import JavaFxBridgeUnavailable


class ObjectIdentityWorkbench:
    """Window-rooted capture tree and structured object-property editor."""

    def __init__(self, app, captured, *, recorded_captures=()):
        self.app = app
        self.original_capture = captured
        self.recorded_captures = tuple(recorded_captures)
        self.context = None
        self.nodes = {}
        self.names = {}
        self.identity_overrides = {}
        self.identity_fields = []
        self.ordinal_field = None
        self.name_entry = None
        self.selected_key = None
        self._loading = True
        self._highlight_generation = 0

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
        caption = Gtk.Label(label="Capture Scope")
        caption.set_halign(Gtk.Align.START)
        left.pack_start(caption, False, False, 0)

        self.tree_store = Gtk.TreeStore(bool, str, str, bool)
        self.tree = Gtk.TreeView(model=self.tree_store)
        toggle = Gtk.CellRendererToggle()
        toggle.connect("toggled", self._toggle_path)
        self.tree.append_column(Gtk.TreeViewColumn("Use", toggle, active=0, activatable=3))
        renderer = Gtk.CellRendererText()
        self.tree.append_column(Gtk.TreeViewColumn("Object", renderer, text=1, sensitive=3))
        self.tree.get_selection().connect("changed", self._selection_changed)
        left.pack_start(self._scrolled(self.tree), True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        pane.pack2(right, resize=True, shrink=False)
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
                context = (
                    build_recording_context(self.recorded_captures)
                    if self.recorded_captures
                    else build_capture_context(self.original_capture)
                )
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
        checked = context.target_keys or (context.target_key,)
        for key in checked:
            self._set_checked(key, True)
        self._select_key(context.target_key)
        self._set_status(
            "Recording scope loaded — %d interacted object(s) checked" % len(checked)
            if self.recorded_captures else "Capture scope loaded"
        )
        return False

    def _context_failed(self, error):
        self._loading = False
        self._set_status("Capture scope unavailable")
        self.app._error("Capture scope", "%s: %s" % (type(error).__name__, error))
        return False

    def _append_tree(self, parent_iter, node):
        iterator = self.tree_store.append(parent_iter, (
            bool(node.is_target and node.is_semantic), node.label, node.key, bool(node.is_semantic),
        ))
        for child in node.children:
            self._append_tree(iterator, child)
        return iterator

    def _toggle_path(self, _renderer, path):
        iterator = self.tree_store.get_iter(path)
        if not self.tree_store.get_value(iterator, 3):
            return
        current = bool(self.tree_store.get_value(iterator, 0))
        self.tree_store.set_value(iterator, 0, not current)

    def _selection_changed(self, selection):
        model, iterator = selection.get_selected()
        if iterator is None:
            return
        self._remember_selected_edits()
        key = model.get_value(iterator, 2)
        self.selected_key = key
        node = self.nodes.get(key)
        if node is None:
            return
        self.selection_caption.set_text(self._selection_summary(node))
        self._render_properties(node)

    def _remember_selected_edits(self):
        if self.selected_key is None:
            return
        if self.name_entry is not None:
            name = self.name_entry.get_text().strip()
            if name:
                self.names[self.selected_key] = name
        if self.identity_fields:
            try:
                self.identity_overrides[self.selected_key] = self.current_identity()
            except Exception:
                # An unfinished edit should not prevent navigation through the
                # capture tree. Save remains the validation boundary.
                pass

    def _selection_summary(self, node):
        kind = "Window root" if node.is_window_root else ("Object" if node.is_semantic else "Structural context")
        if node.is_target:
            kind += " · captured target"
        peer_count = len(self.context.selected_group(node.key)) if self.context else 0
        return "%s · %s peer(s) in semantic parent scope" % (kind, peer_count)

    def _render_properties(self, node):
        for child in self.properties_box.get_children():
            self.properties_box.remove(child)
        self.identity_fields = []
        self.ordinal_field = None
        self.name_entry = None

        captured = None
        try:
            captured = self._captured_for_node(node)
            strategy = captured.candidate_strategy()
            identity = self.identity_overrides.get(node.key)
            if identity is None:
                identity = (
                    strategy.options.get("identification")
                    if strategy.type == "javafx"
                    else captured.candidate_identification().to_dict()
                )
            if not isinstance(identity, Mapping):
                identity = {"mandatory": {}}
        except Exception as exc:
            self._add_message("Identity unavailable: %s: %s" % (type(exc).__name__, exc))
            identity = {"mandatory": {}}

        inherited = self.context.inherited_descriptors(node.key) if self.context else {}
        common = self.context.common_peer_descriptors(node.key) if self.context else {}
        framework = str(getattr(captured, "framework", "") or "") if captured is not None else (
            self.context.framework if self.context else ""
        )
        self._build_property_inventory(node, identity, inherited, common, framework)
        self.properties_box.show_all()

    def _build_property_inventory(self, node, identity, inherited, common, framework):
        frame = Gtk.Frame(label="Object Properties")
        grid = Gtk.Grid()
        grid.set_border_width(8)
        grid.set_row_spacing(5)
        grid.set_column_spacing(8)
        frame.add(grid)
        self.properties_box.pack_start(frame, False, False, 0)

        headers = ("Use", "Property", "", "Value", "Stability / scope")
        for column, text in enumerate(headers):
            label = Gtk.Label(label=text)
            label.set_halign(Gtk.Align.START)
            grid.attach(label, column, 0, 1, 1)

        row = 1
        name = self.names.get(node.key)
        if not name:
            name = self._default_component_id(node)
            self.names[node.key] = name
        grid.attach(Gtk.Label(label=""), 0, row, 1, 1)
        name_label = Gtk.Label(label="name")
        name_label.set_halign(Gtk.Align.START)
        grid.attach(name_label, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="="), 2, row, 1, 1)
        self.name_entry = Gtk.Entry()
        self.name_entry.set_hexpand(True)
        self.name_entry.set_text(name)
        self.name_entry.connect("changed", self._name_changed)
        grid.attach(self.name_entry, 3, row, 1, 1)
        source = Gtk.Label(label="authored semantic name")
        source.set_halign(Gtk.Align.START)
        grid.attach(source, 4, row, 1, 1)
        row += 1

        known_classes = _known_classes("javafx" if framework == "javafx" else "", identity)
        candidate_keys = set()

        for section in ("mandatory", "assistive"):
            values = identity.get(section, {})
            if not isinstance(values, Mapping):
                continue
            for path, value in _flatten(values):
                logical_key = _logical_leaf(path)
                candidate_keys.add(logical_key)
                scope = _scope_for(logical_key, value, inherited, common)
                policy = property_policy(
                    logical_key,
                    value,
                    candidate_section=section,
                    source=scope,
                    framework=framework,
                )
                row = self._add_property_row(
                    grid,
                    row,
                    section,
                    path,
                    logical_key,
                    value,
                    policy,
                    known_classes,
                )

        ordinal = identity.get("ordinal")
        if isinstance(ordinal, int) and not isinstance(ordinal, bool) and ordinal >= 0:
            policy = property_policy(
                "ordinal",
                ordinal,
                candidate_section="assistive",
                source="local",
                framework=framework,
            )
            check = Gtk.CheckButton()
            check.set_active(True)
            grid.attach(check, 0, row, 1, 1)
            label = Gtk.Label(label="ordinal")
            label.set_halign(Gtk.Align.START)
            grid.attach(label, 1, row, 1, 1)
            grid.attach(Gtk.Label(label="="), 2, row, 1, 1)
            spin = Gtk.SpinButton.new_with_range(0, 100000, 1)
            spin.set_value(ordinal)
            grid.attach(spin, 3, row, 1, 1)
            status = Gtk.Label(label="%s · final discriminator" % policy.stability)
            status.set_halign(Gtk.Align.START)
            grid.attach(status, 4, row, 1, 1)
            self.ordinal_field = (check, spin)
            candidate_keys.add("ordinal")
            row += 1

        # Broad capture inventory: weak and runtime values remain available but
        # are not silently selected as identity. Session plumbing is visible
        # and permanently non-selectable.
        for logical_key, value in sorted(available_properties(node.payload).items()):
            if logical_key in candidate_keys:
                continue
            scope = _scope_for(logical_key, value, inherited, common)
            policy = property_policy(
                logical_key,
                value,
                candidate_section=None,
                source=scope,
                framework=framework,
            )
            path = _path_from_logical_key(logical_key)
            row = self._add_property_row(
                grid,
                row,
                "assistive",
                path,
                logical_key,
                value,
                policy,
                known_classes,
            )

        note = Gtk.Label(
            label=(
                "Capture collects broadly. Stable identity is selected conservatively; "
                "weak/runtime evidence is available but off by default. Session-only "
                "bridge metadata is diagnostic and cannot be authored."
            )
        )
        note.set_halign(Gtk.Align.START)
        note.set_line_wrap(True)
        note.set_sensitive(False)
        grid.attach(note, 0, row, 5, 1)

    def _add_property_row(self, grid, row, section, path, logical_key, value, policy, known_classes):
        check = Gtk.CheckButton()
        check.set_active(bool(policy.selected))
        check.set_sensitive(bool(policy.selectable))
        grid.attach(check, 0, row, 1, 1)

        key_label = Gtk.Label(label=logical_key)
        key_label.set_halign(Gtk.Align.START)
        grid.attach(key_label, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="="), 2, row, 1, 1)

        field, entry = _value_field(logical_key, value, known_classes)
        field.set_hexpand(True)
        field.set_sensitive(bool(policy.selectable))
        grid.attach(field, 3, row, 1, 1)

        status = Gtk.Label(label="%s · %s" % (policy.stability, policy.reason))
        status.set_halign(Gtk.Align.START)
        grid.attach(status, 4, row, 1, 1)
        if not policy.selectable:
            key_label.set_sensitive(False)
            status.set_sensitive(False)

        self.identity_fields.append((section, path, value, check, entry, policy.selectable))
        return row + 1

    def _name_changed(self, entry):
        if self.selected_key is None:
            return
        value = entry.get_text().strip()
        if value:
            self.names[self.selected_key] = value
            iterator = self._iter_for_key(self.selected_key)
            if iterator is not None:
                self.tree_store.set_value(iterator, 1, value)

    def _add_message(self, text):
        label = Gtk.Label(label=text)
        label.set_halign(Gtk.Align.START)
        label.set_line_wrap(True)
        self.properties_box.pack_start(label, False, False, 0)

    def current_identity(self):
        leaves = {"mandatory": {}, "assistive": {}}
        for section, path, original, check, entry, _selectable in self.identity_fields:
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
        if node is None or not node.is_semantic:
            return
        try:
            identity = self.current_identity()
            original = self._captured_for_node(node)
        except Exception as exc:
            return self.app._error("Highlight failed", "%s: %s" % (type(exc).__name__, exc))
        self._highlight_generation += 1
        generation = self._highlight_generation
        self._set_status("Resolving %s for highlight…" % node.label)

        def worker():
            try:
                if str(getattr(original, "framework", "") or "") == "javafx":
                    properties = dict(getattr(original, "backend_properties", {}) or {})
                    process_id = properties.get("bridge_pid")
                    try:
                        resolved = self.app.capture.javafx_driver.inspect(
                            identification=identity, process_id=process_id,
                        )
                    except JavaFxBridgeUnavailable:
                        resolved = self.app.capture.javafx_driver.inspect(identification=identity)
                else:
                    resolved = self.app.capture.capture_by_locator(identification=identity)
                if resolved.bounds is None:
                    raise LookupError("resolved object has no screen bounds")
            except Exception as exc:
                GLib.idle_add(self._highlight_resolution_failed, generation, exc)
            else:
                GLib.idle_add(self._highlight_resolution_ready, generation, resolved)

        threading.Thread(
            target=worker,
            name="workbench-highlight-resolver",
            daemon=True,
        ).start()

    def _highlight_resolution_ready(self, generation, captured):
        if generation != self._highlight_generation:
            return False
        rect = tuple(int(round(float(value))) for value in captured.bounds)
        self.app._show_highlight(rect, False)
        GLib.timeout_add(1400, self._clear_highlight)
        self._set_status("Highlighted resolved object")
        return False

    def _highlight_resolution_failed(self, generation, error):
        if generation != self._highlight_generation:
            return False
        self._set_status("Highlight resolution failed")
        self.app._error("Highlight failed", "%s: %s" % (type(error).__name__, error))
        return False

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
            if child.is_semantic:
                self._set_checked(child.key, True)
        self._set_status("Checked semantic sibling group")

    def check_branch(self):
        node = self._selected_node()
        if node is None:
            return
        for item in node.walk():
            if item.is_semantic:
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
        if not node.is_semantic:
            return self.app._info("Save object", "Structural context identifies semantic descendants but cannot be saved as an object.")
        try:
            identity = self.current_identity()
            self.identity_overrides[node.key] = identity
            component_id = (self.name_entry.get_text() if self.name_entry is not None else "").strip()
            if not component_id:
                raise ValueError("name is required")
            self.names[node.key] = component_id
            self._save_node(node, component_id, identity)
            self._set_status("Saved %s to working repository" % component_id)
        except Exception as exc:
            self.app._error("Save object", "%s: %s" % (type(exc).__name__, exc))

    def save_checked(self):
        checked = self._checked_keys()
        if not checked:
            return self.app._info("Batch save", "Check one or more objects in the capture tree first.")
        self._remember_selected_edits()
        saved = 0
        errors = []
        for key in checked:
            node = self.nodes.get(key)
            if node is None or not node.is_semantic:
                continue
            try:
                captured = self._captured_for_node(node)
                identity = self.identity_overrides.get(key)
                if identity is None:
                    strategy = captured.candidate_strategy()
                    identity = (
                        strategy.options.get("identification")
                        if strategy.type == "javafx"
                        else captured.candidate_identification().to_dict()
                    )
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
        if not node.is_semantic:
            raise ValueError("structural context cannot be saved as an object")
        captured = self._captured_for_node(node)
        if hasattr(self.app, "bind_captured_application") and not self.app.bind_captured_application(captured):
            raise ValueError("captured object belongs to an application that is not the current test target")
        existing = self.app.repository.components.get(component_id)
        revision = 1 if existing is None else existing.revision + 1
        definition = self.app.capture.definition_from_capture(
            component_id,
            captured,
            identification=identity,
            revision=revision,
            validate_live=not bool(self.recorded_captures),
        )
        self.app.repository = self.app.repository.with_component(definition)
        if self.recorded_captures and hasattr(self.app, "recorded_capture_saved"):
            self.app.recorded_capture_saved(captured, component_id)
        if hasattr(self.app, "_mark_repository_dirty"):
            self.app._mark_repository_dirty(True)
        self.app.refresh_objects()

    def save_repository(self):
        if hasattr(self.app, "save_repository"):
            self.app.save_repository()

    def _captured_for_node(self, node):
        mapped = self.context.captured_by_key.get(node.key) if self.context else None
        if mapped is not None:
            return mapped
        original_ref = str(self.original_capture.backend_properties.get("node_ref") or "")
        if node.key == self.context.target_key and (not original_ref or node.key == original_ref):
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
        if self.tree_store.get_value(iterator, 0) and self.tree_store.get_value(iterator, 3):
            result.append(self.tree_store.get_value(iterator, 2))
        child = self.tree_store.iter_children(iterator)
        while child is not None:
            self._collect_checked(child, result)
            child = self.tree_store.iter_next(child)

    def _set_checked(self, key, value):
        iterator = self._iter_for_key(key)
        if iterator is not None and self.tree_store.get_value(iterator, 3):
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


def open_capture_workbench(app, captured, *, recorded_captures=()):
    existing = getattr(app, "_capture_workbench", None)
    if existing is not None:
        try:
            existing.window.destroy()
        except Exception:
            pass
    workbench = ObjectIdentityWorkbench(app, captured, recorded_captures=recorded_captures)
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
    entry.set_text(_editable_text(value))
    return entry, entry


def _editable_text(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, separators=(",", ":"), default=str)
    return str(value)


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
    if root == "lineage":
        return "lineage"
    return str(path[-1])


def _path_from_logical_key(key):
    if key.startswith("layout."):
        return ("layout", key[len("layout."):])
    if key.startswith("properties."):
        return ("properties", key[len("properties."):])
    return (key,)


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
    if isinstance(original, (dict, list, tuple)):
        try:
            value = json.loads(text)
        except ValueError:
            raise ValueError("%s must be valid JSON" % label)
        if isinstance(original, dict) and not isinstance(value, dict):
            raise ValueError("%s must remain an object" % label)
        if isinstance(original, (list, tuple)) and not isinstance(value, list):
            raise ValueError("%s must remain a list" % label)
        return value
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
        _assign_path(root, path, value)
    return root


def _assign_path(root, path, value):
    token = path[0]
    if isinstance(token, int):
        raise ValueError("identity cannot begin with an array index")
    if len(path) == 1:
        root[token] = value
        return
    next_token = path[1]
    if isinstance(next_token, int):
        values = root.setdefault(token, [])
        while len(values) <= next_token:
            values.append({})
        if len(path) == 2:
            values[next_token] = value
            return
        child = values[next_token]
        if not isinstance(child, dict):
            child = {}
            values[next_token] = child
        _assign_path(child, path[2:], value)
        return
    child = root.setdefault(token, {})
    if not isinstance(child, dict):
        child = {}
        root[token] = child
    _assign_path(child, path[1:], value)
