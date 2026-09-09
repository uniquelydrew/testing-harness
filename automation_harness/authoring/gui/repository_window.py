from __future__ import annotations

import json

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.authoring.gui.common import ArtifactWindow
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService


class ObjectRepositoryWindow(ArtifactWindow):
    title_prefix = "Automation Harness Object Repository"

    def __init__(self, path, *, project_context=None, opener=None):
        super().__init__(path, project_context=project_context, opener=opener)
        self.repository = ComponentRepository.load((self.path,))
        self.capture = HybridObjectCaptureService()
        self.button("Save", self.save)
        self.button("Capture at Pointer (2s)", self.capture_pointer_delayed)
        self.button("Refresh", self.reload)
        self.button("Duplicate", self.duplicate_selected)
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

    def remove_selected(self):
        component_id = self.selected(self.tree)
        if component_id and self.confirm("Remove Object", "Remove %s from this repository?" % component_id):
            self.repository = self.repository.without_component(component_id); self.mark_dirty(); self.refresh()

    def open_project(self):
        if self.project_context:
            return self.open_artifact(self.project_context, project_context=self.project_context)
        self.info("Project", "This repository was opened without Project context.")
