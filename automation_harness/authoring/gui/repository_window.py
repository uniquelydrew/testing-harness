from __future__ import annotations

import json

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.authoring.gui.common import ArtifactWindow
from automation_harness.authoring.plan_repository import merge_objects_or
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService
from automation_harness.core.pointer_actions import click_bounds
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver


class ObjectRepositoryWindow(ArtifactWindow):
    title_prefix = "Automation Harness Object Repository"

    def __init__(self, path, *, project_context=None, opener=None):
        super().__init__(path, project_context=project_context, opener=opener)
        self.repository = ComponentRepository.load((self.path,))
        self.capture = HybridObjectCaptureService()
        self._highlight_windows = []
        self.button("Save", self.save)
        self.button("Capture at Pointer (2s)", self.capture_pointer_delayed)
        self.button("Highlight", self.highlight_selected)
        self.button("Click", self.click_selected)
        self.button("Refresh", self.reload)
        self.button("Duplicate", self.duplicate_selected)
        self.button("Merge / Deduplicate", self.merge_deduplicate)
        self.button("Remove", self.remove_selected)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.root.pack_start(paned, True, True, 0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        paned.pack1(left, resize=True, shrink=False)
        self.search = Gtk.SearchEntry(); self.search.set_placeholder_text("Object ID, type, framework, description")
        self.search.connect("search-changed", lambda *_args: self.refresh())
        left.pack_start(self.search, False, False, 0)
        self.tree, self.store = self.list_tree((("Component", 300), ("Type", 140), ("Framework", 140), ("Revision", 80)))
        self.tree.get_selection().connect("changed", lambda *_args: self.show_selected())
        left.pack_start(self.scrolled(self.tree), True, True, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        paned.pack2(right, resize=True, shrink=False)
        self.detail = Gtk.TextView(); self.detail.set_editable(False); self.detail.set_monospace(True)
        right.pack_start(self.scrolled(self.detail), True, True, 0)
        row = Gtk.Box(spacing=6); right.pack_start(row, False, False, 0)
        self.button("Edit Definition", self.edit_selected, parent=row)
        self.button("Highlight", self.highlight_selected, parent=row)
        self.button("Click", self.click_selected, parent=row)
        self.button("Open Project", self.open_project, parent=row)
        paned.set_position(620)
        self.refresh()

    def refresh(self):
        selected = self.selected(self.tree)
        query = self.search.get_text().strip().casefold()
        self.store.clear(); visible = 0
        for component_id, definition in sorted(self.repository.components.items()):
            searchable = " ".join((component_id, definition.description, definition.object_type.value, definition.framework or "")).casefold()
            if query and query not in searchable:
                continue
            self.store.append((component_id, definition.object_type.value, definition.framework or "", str(definition.revision)))
            visible += 1
        self.set_status("%d of %d objects" % (visible, len(self.repository.components)) if query else "%d objects" % visible)
        if selected:
            model = self.tree.get_model(); iterator = model.get_iter_first()
            while iterator is not None:
                if model.get_value(iterator, 0) == selected:
                    self.tree.get_selection().select_iter(iterator); break
                iterator = model.iter_next(iterator)
        self.show_selected()

    def show_selected(self):
        component_id = self.selected(self.tree)
        if not component_id:
            self.detail.get_buffer().set_text(""); return
        definition = self.repository.get(component_id)
        payload = self.repository.to_document()["components"][definition.component_id]
        self.detail.get_buffer().set_text(json.dumps({"component_id": component_id, **payload}, indent=2, default=str))

    def capture_pointer_delayed(self):
        if not getattr(self.capture, "available", True):
            return self.error("Object Capture", "No supported live desktop capture backend is available.")
        self.set_status("Move the pointer over the target object…")
        self.window.hide()
        GLib.timeout_add(2000, self._capture_pointer_now)

    def _capture_pointer_now(self):
        try:
            display = Gdk.Display.get_default(); seat = display.get_default_seat(); pointer = seat.get_pointer(); _screen, x, y = pointer.get_position()
            captured = self.capture.capture_scoped_at_point(int(x), int(y))
        except Exception as exc:
            self.window.show_all(); self.window.present(); self.error("Object Capture", "%s: %s" % (type(exc).__name__, exc)); self.set_status("Capture failed"); return False
        self.window.show_all(); self.window.present()
        component_id = self.ask_text("Save Captured Object", "Logical component ID:")
        if not component_id:
            self.set_status("Capture discarded"); return False
        try:
            definition = self.capture.definition_from_capture(component_id, captured)
            self.repository = self.repository.with_component(definition)
        except Exception as exc:
            self.error("Object Capture", "%s: %s" % (type(exc).__name__, exc)); return False
        self.mark_dirty(); self.refresh(); self.set_status("Captured %s — save repository to persist" % component_id); return False

    def highlight_selected(self):
        component_id = self.selected(self.tree)
        if not component_id:
            return self.info("Highlight Object", "Select an object first.")
        self.set_status("Resolving %s…" % component_id)
        self.window.hide()
        GLib.timeout_add(140, self._highlight_selected_now, component_id)

    def _highlight_selected_now(self, component_id):
        try:
            resolved = self._resolve_live_component(component_id, activate_window=True)
            bounds = self._resolved_bounds(resolved)
            self._show_highlight(bounds)
            self.set_status("Highlighted %s using %s" % (component_id, resolved.strategy))
            GLib.timeout_add(1400, self._finish_highlight)
        except Exception as exc:
            self.window.show_all(); self.window.present()
            self.set_status("Highlight failed")
            self.error("Highlight Object", "%s: %s" % (type(exc).__name__, exc))
        return False

    def _finish_highlight(self):
        self._clear_highlight()
        self.window.show_all(); self.window.present()
        return False

    def click_selected(self):
        component_id = self.selected(self.tree)
        if not component_id:
            return self.info("Click Object", "Select an object first.")
        definition = self.repository.get(component_id)
        if not definition.supports("click"):
            return self.error("Click Object", "Object %s does not support Click." % component_id)
        self.set_status("Resolving %s for click…" % component_id)
        self.window.hide()
        GLib.timeout_add(140, self._click_selected_now, component_id)

    def _click_selected_now(self, component_id):
        try:
            resolved = self._resolve_live_component(component_id, activate_window=True)
            bounds = self._resolved_bounds(resolved)
            click_bounds(bounds, "click")
            self.set_status("Clicked %s using resolved %s bounds" % (component_id, resolved.strategy))
        except Exception as exc:
            self.set_status("Click failed")
            self.error("Click Object", "%s: %s" % (type(exc).__name__, exc))
        finally:
            self.window.show_all(); self.window.present()
        return False

    def _resolve_live_component(self, component_id, activate_window=False):
        definition = self.repository.get(component_id)
        errors = []
        for strategy in definition.strategies:
            options = dict(strategy.options)
            identification = options.get("identification")
            try:
                if strategy.type == "javafx":
                    driver = JavaFxBridgeDriver()
                    if activate_window:
                        driver.activate_window(identification=identification)
                    return driver.resolve(component_id, identification=identification)
                if strategy.type == "java_accessibility":
                    driver = JavaAccessibilityDriver()
                    if activate_window:
                        driver.activate_window(identification=identification)
                    return driver.resolve(component_id, identification=identification)
                if strategy.type == "atspi":
                    driver = AtspiDriver()
                    kwargs = {
                        "identification": identification,
                        "name": options.get("name"),
                        "role": options.get("role"),
                        "accessible_id": options.get("accessible_id"),
                    }
                    if activate_window:
                        driver.activate_window(**kwargs)
                    return driver.resolve(component_id, **kwargs)
                errors.append("%s: unsupported live verification strategy" % strategy.type)
            except Exception as exc:
                errors.append("%s: %s: %s" % (strategy.type, type(exc).__name__, exc))
        raise RuntimeError("unable to resolve %r; %s" % (component_id, "; ".join(errors or ["no strategies"])))

    @staticmethod
    def _resolved_bounds(resolved):
        bounds = resolved.metadata.get("bounds")
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError("resolved component does not expose four-value desktop bounds")
        values = tuple(int(round(float(value))) for value in bounds)
        if values[2] <= 0 or values[3] <= 0:
            raise ValueError("resolved component bounds must have positive width and height")
        return values

    def _show_highlight(self, bounds):
        self._clear_highlight()
        x, y, width, height = bounds
        thickness = 4
        provider = Gtk.CssProvider(); provider.load_from_data(b"* { background-color: #ff3b30; }")
        rectangles = (
            (x, y, width, thickness),
            (x, y + max(0, height - thickness), width, thickness),
            (x, y, thickness, height),
            (x + max(0, width - thickness), y, thickness, height),
        )
        for rx, ry, rw, rh in rectangles:
            edge = Gtk.Window(type=Gtk.WindowType.POPUP)
            edge.set_decorated(False); edge.set_keep_above(True); edge.set_accept_focus(False)
            edge.set_opacity(0.88); edge.move(rx, ry); edge.resize(max(1, rw), max(1, rh))
            edge.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            edge.show_all(); self._highlight_windows.append(edge)

    def _clear_highlight(self):
        for edge in tuple(self._highlight_windows):
            try: edge.destroy()
            except Exception: pass
        self._highlight_windows = []

    def save(self):
        self.repository.save(self.path); self.mark_dirty(False); self.set_status("Saved object repository")

    def reload(self):
        if self.dirty and not self.confirm("Reload", "Discard unsaved repository changes?"):
            return
        self.repository = ComponentRepository.load((self.path,)); self.mark_dirty(False); self.refresh()

    def edit_selected(self):
        component_id = self.selected(self.tree)
        if not component_id:
            return self.info("Object Repository", "Select an object first.")
        current = self.repository.to_document()["components"][component_id]
        dialog = Gtk.Dialog(title="Edit %s" % component_id, transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Apply", Gtk.ResponseType.OK)
        text = Gtk.TextView(); text.set_monospace(True); text.get_buffer().set_text(json.dumps(current, indent=2))
        scroll = self.scrolled(text); scroll.set_size_request(720, 520); dialog.get_content_area().pack_start(scroll, True, True, 0)
        dialog.show_all(); response = dialog.run()
        buffer = text.get_buffer(); raw = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True); dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            value = json.loads(raw)
            parsed = ComponentRepository.from_document({"version": 3, "components": {component_id: value}}, source="object editor")
            self.repository = self.repository.with_component(parsed.get(component_id))
        except Exception as exc:
            return self.error("Object Repository", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(); self.refresh()

    def duplicate_selected(self):
        component_id = self.selected(self.tree)
        if not component_id:
            return
        new_id = self.ask_text("Duplicate Object", "New component ID:", component_id + ".copy")
        if not new_id:
            return
        try:
            raw = self.repository.to_document()["components"][component_id]
            raw = dict(raw); raw.pop("object_id", None)
            parsed = ComponentRepository.from_document({"version": 2, "components": {new_id: raw}}, source="duplicate")
            self.repository = self.repository.with_component(parsed.get(new_id))
        except Exception as exc:
            return self.error("Duplicate Object", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(); self.refresh()

    def merge_deduplicate(self):
        if len(self.repository.components) < 2:
            return self.info("Merge Objects", "At least two repository objects are required.")
        dialog = Gtk.Dialog(title="Merge / Deduplicate Objects", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Merge", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        keep = Gtk.ComboBoxText(); duplicate = Gtk.ComboBoxText()
        ids = sorted(self.repository.components)
        for component_id in ids:
            keep.append(component_id, component_id); duplicate.append(component_id, component_id)
        selected = self.selected(self.tree)
        keep.set_active_id(selected if selected in ids else ids[0]); duplicate.set_active(1 if len(ids) > 1 else 0)
        for label_text, widget in (("Canonical object to keep", keep), ("Duplicate / alternate object", duplicate)):
            label = Gtk.Label(label=label_text); label.set_xalign(0); box.pack_start(label, False, False, 0); box.pack_start(widget, False, False, 0)
        note = Gtk.Label(label="The canonical object keeps its immutable ID. Locator strategies are unioned as OR alternatives; the duplicate entry is removed.")
        note.set_line_wrap(True); note.set_xalign(0); box.pack_start(note, False, False, 0)
        dialog.show_all(); response = dialog.run(); target = keep.get_active_id(); source = duplicate.get_active_id(); dialog.destroy()
        if response != Gtk.ResponseType.OK: return
        if not target or not source or target == source:
            return self.error("Merge Objects", "Choose two different objects.")
        if not self.confirm("Merge Objects", "Merge %s into %s and remove %s?" % (source, target, source)):
            return
        try:
            self.repository = merge_objects_or(self.repository, target, (source,))
        except Exception as exc:
            return self.error("Merge Objects", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(); self.refresh(); self.set_status("Merged %s into %s using OR locator strategies" % (source, target))

    def remove_selected(self):
        component_id = self.selected(self.tree)
        if component_id and self.confirm("Remove Object", "Remove %s from this repository?" % component_id):
            self.repository = self.repository.without_component(component_id); self.mark_dirty(); self.refresh()

    def open_project(self):
        if self.project_context:
            return self.open_artifact(self.project_context, project_context=self.project_context)
        self.info("Project", "This repository was opened without Project context.")
