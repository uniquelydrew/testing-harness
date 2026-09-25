"""Direct capture and repository reparenting."""
from __future__ import annotations

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.capture_boundaries import classify_capture_boundary, surface_relative_visual_capture
from automation_harness.core.object_reparenting import reparent_leaf
from automation_harness.core.captured_repository import materialize_capture

_INSTALLED = False
_TARGET = Gtk.TargetEntry.new("automation-harness/repository-object", Gtk.TargetFlags.SAME_APP, 0)


def install():
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    from automation_harness.authoring.gui.repository_identity_workbench_window import (
        RepositoryIdentityWorkbench,
        RepositoryIdentityWorkbenchWindow,
    )
    original_toolbar = RepositoryIdentityWorkbench._configure_repository_toolbar
    original_context_ready = RepositoryIdentityWorkbench._context_ready
    original_window_init = RepositoryIdentityWorkbenchWindow.__init__

    def configure_toolbar(self):
        original_toolbar(self)
        self._button(self.toolbar, "Capture New Object", self.capture_new_object)
        self.window.show_all()

    def context_ready(self, context):
        result = original_context_ready(self, context)
        _install_drag_reparent(self)
        return result

    def window_init(self, path, *, project_context=None, opener=None):
        original_window_init(self, path, project_context=project_context, opener=opener)
        self.host.project_context = Path(project_context).resolve() if project_context else None

    RepositoryIdentityWorkbench._configure_repository_toolbar = configure_toolbar
    RepositoryIdentityWorkbench._context_ready = context_ready
    RepositoryIdentityWorkbench.capture_new_object = capture_new_object
    RepositoryIdentityWorkbench._capture_new_finished = _capture_new_finished
    RepositoryIdentityWorkbench._add_captured_object = _add_captured_object
    RepositoryIdentityWorkbenchWindow.__init__ = window_init


def capture_new_object(self):
    if not getattr(self.app.capture, "available", True):
        return self.app._error("Capture object", "No supported live desktop capture backend is available.")
    self._set_status("Capture new object: click the live target…")
    self.window.hide()

    def worker():
        try:
            captured = self.app.capture.capture_next_click(click_count=1, timeout=30.0)
        except Exception as exc:
            GLib.idle_add(self._capture_new_finished, None, exc)
        else:
            GLib.idle_add(self._capture_new_finished, captured, None)

    threading.Thread(target=worker, name="repository-new-object-capture", daemon=True).start()


def _capture_new_finished(self, captured, error):
    self.window.show_all(); self.window.present()
    if error is not None:
        self._set_status("Capture new object failed")
        self.app._error("Capture object", "%s: %s" % (type(error).__name__, error)); return False
    dialog = Gtk.Dialog(title="Add Captured Object", transient_for=self.window, modal=True)
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
    box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
    box.pack_start(Gtk.Label(label="Logical component ID:"), False, False, 0)
    entry = Gtk.Entry(); proposed = str(getattr(captured, "name", None) or getattr(captured, "accessible_id", None) or "Object")
    entry.set_text(_segment(proposed)); entry.set_activates_default(True); box.pack_start(entry, False, False, 0)
    visual_leaf = None
    boundary = classify_capture_boundary(captured)
    if boundary.supports_visual_children:
        visual_leaf = Gtk.CheckButton(label="Capture a rendered visual region at the click point")
        visual_leaf.set_active(True)
        box.pack_start(visual_leaf, False, False, 0)
    note = Gtk.Label(label="The captured locator is added directly to this Object Repository. Drag the new leaf onto a concrete repository object to reparent it.")
    note.set_line_wrap(True); note.set_halign(Gtk.Align.START); box.pack_start(note, False, False, 0)
    dialog.set_default_response(Gtk.ResponseType.OK); dialog.show_all()
    response = dialog.run()
    component_id = entry.get_text().strip()
    capture_visual_leaf = visual_leaf is not None and visual_leaf.get_active()
    dialog.destroy()
    if response != Gtk.ResponseType.OK:
        self._set_status("Capture discarded"); return False
    if capture_visual_leaf:
        try:
            captured = surface_relative_visual_capture(captured)
        except Exception as exc:
            self.app._error("Capture visual object", "%s: %s" % (type(exc).__name__, exc)); return False
    return self._add_captured_object(component_id, captured)


def _add_captured_object(self, component_id, captured):
    if not component_id:
        self.app._error("Capture object", "Logical component ID is required."); return False
    repository = self._repository_host.repository
    if component_id in repository.components:
        self.app._error("Capture object", "Object %r already exists." % component_id); return False
    try:
        repository, definition, _created = materialize_capture(
            self.app.capture, repository, component_id, captured,
            visual_leaf=captured.candidate_strategy().type == "anchored_visual",
        )
    except Exception as exc:
        self.app._error("Capture object", "%s: %s" % (type(exc).__name__, exc)); return False
    self._repository_host.repository = repository; self.app.repository = repository
    self.app._mark_repository_dirty(True)
    self._set_status("Added %s — Save Repository to persist" % component_id)
    self._load_context_async(); return False


def _install_drag_reparent(workbench):
    if getattr(workbench, "_repository_drag_installed", False):
        return
    workbench._repository_drag_installed = True; workbench._repository_drag_source_key = None
    workbench.tree.enable_model_drag_source(Gdk.ModifierType.BUTTON1_MASK, [_TARGET], Gdk.DragAction.MOVE)
    workbench.tree.enable_model_drag_dest([_TARGET], Gdk.DragAction.MOVE)
    workbench.tree.connect("drag-begin", lambda tree, context: _drag_begin(workbench, tree))
    workbench.tree.connect("drag-data-get", lambda tree, context, selection, info, time: _drag_data_get(workbench, selection))
    workbench.tree.connect("drag-data-received", lambda tree, context, x, y, selection, info, time: _drag_received(workbench, tree, context, x, y, time))


def _drag_begin(workbench, tree):
    model, iterator = tree.get_selection().get_selected()
    workbench._repository_drag_source_key = model.get_value(iterator, 2) if iterator is not None else None


def _drag_data_get(workbench, selection):
    if workbench._repository_drag_source_key:
        selection.set_text(str(workbench._repository_drag_source_key), -1)


def _drag_received(workbench, tree, context, x, y, time):
    success = False
    try:
        source_key = workbench._repository_drag_source_key; destination = tree.get_dest_row_at_pos(x, y)
        if not source_key or destination is None:
            return
        path, _position = destination; iterator = tree.get_model().get_iter(path)
        target_key = tree.get_model().get_value(iterator, 2)
        source = workbench._definition_by_key.get(source_key); target = workbench._definition_by_key.get(target_key)
        if source is None or target is None:
            workbench.app._info("Reparent object", "Drag a repository leaf onto a concrete repository object. Logical grouping nodes cannot become parents."); return
        updated, old_id, new_id = reparent_leaf(workbench._repository_host.repository, source.component_id, target.component_id)
        workbench._repository_host.repository = updated; workbench.app.repository = updated
        workbench.app._mark_repository_dirty(True)
        workbench._set_status("Reparented %s under %s as %s — Save Repository to persist" % (old_id, target.component_id, new_id))
        workbench._load_context_async(); success = True
    except Exception as exc:
        workbench.app._error("Reparent object", "%s: %s" % (type(exc).__name__, exc))
    finally:
        workbench._repository_drag_source_key = None
        context.finish(success, False, time)


def _segment(value):
    output = []; capitalize = True
    for char in str(value or "").strip():
        if char.isalnum():
            output.append(char.upper() if capitalize else char); capitalize = False
        else:
            capitalize = True
    return "".join(output) or "Object"
