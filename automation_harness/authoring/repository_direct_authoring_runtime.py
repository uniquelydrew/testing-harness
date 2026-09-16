"""Direct capture, reparenting, and project-wide reference propagation."""
from __future__ import annotations

from pathlib import Path
import tempfile
import threading

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.authoring.object_reference_updates import apply_project_reference_updates
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_reparenting import reparent_leaf

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
    original_save_repository = RepositoryIdentityWorkbench.save_repository
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

    def save_repository(self):
        """Persist repository + dependent aliases as one project transaction.

        The existing workbench save path is still responsible for validating and
        applying in-memory property/name edits. It is redirected to a temporary
        repository file first. Only after that succeeds do we atomically replace
        the real repository together with every bound Test Plan and Step Registry.
        """
        real_path = Path(self._repository_host.path).resolve()
        project_context = getattr(self._repository_host, "project_context", None)
        if project_context is None:
            return original_save_repository(self)

        before = ComponentRepository.load((real_path,)) if real_path.is_file() else ComponentRepository({})
        temporary = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=".%s.authoring-" % real_path.name,
                suffix=real_path.suffix,
                dir=str(real_path.parent),
            )
            Path(temporary_name).unlink()
            temporary = Path(temporary_name)
            # mkstemp reserved the name. Close the descriptor before the normal
            # repository save recreates it using its own text writer.
            import os
            os.close(fd)

            self._repository_host.path = temporary
            result = original_save_repository(self)
            self._repository_host.path = real_path

            if not temporary.is_file():
                # The wrapped save reported/handled a validation error.
                self.app._mark_repository_dirty(True)
                return result

            after = self._repository_host.repository
            rename_map = _rename_map_by_object_id(before, after)
            report = apply_project_reference_updates(
                project_context,
                real_path,
                rename_map,
                repository=after,
            )
            self.app.repository = after
            self.app._mark_repository_dirty(False)
            dependent_updates = [item for item in report.updates if item.artifact_type != "object_repository"]
            if dependent_updates:
                self._set_status(
                    "Saved repository — updated %d reference(s) across %d dependent artifact(s)" %
                    (sum(item.replacements for item in dependent_updates), len(dependent_updates))
                )
            else:
                self._set_status("Saved repository")
            return result
        except Exception as exc:
            self._repository_host.path = real_path
            self.app._mark_repository_dirty(True)
            self.app._error("Save repository", "%s: %s" % (type(exc).__name__, exc))
            return None
        finally:
            self._repository_host.path = real_path
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    RepositoryIdentityWorkbench._configure_repository_toolbar = configure_toolbar
    RepositoryIdentityWorkbench._context_ready = context_ready
    RepositoryIdentityWorkbench.save_repository = save_repository
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
    note = Gtk.Label(label="The captured locator is added directly to this Object Repository. Drag the new leaf onto a concrete repository object to reparent it.")
    note.set_line_wrap(True); note.set_halign(Gtk.Align.START); box.pack_start(note, False, False, 0)
    dialog.set_default_response(Gtk.ResponseType.OK); dialog.show_all()
    response = dialog.run(); component_id = entry.get_text().strip(); dialog.destroy()
    if response != Gtk.ResponseType.OK:
        self._set_status("Capture discarded"); return False
    return self._add_captured_object(component_id, captured)


def _add_captured_object(self, component_id, captured):
    if not component_id:
        self.app._error("Capture object", "Logical component ID is required."); return False
    repository = self._repository_host.repository
    if component_id in repository.components:
        self.app._error("Capture object", "Object %r already exists." % component_id); return False
    try:
        definition = self.app.capture.definition_from_capture(component_id, captured)
        repository = repository.with_component(definition)
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


def _rename_map_by_object_id(before, after):
    before_names = {definition.object_id: name for name, definition in before.components.items()}
    after_names = {definition.object_id: name for name, definition in after.components.items()}
    return {
        old_name: after_names[object_id]
        for object_id, old_name in before_names.items()
        if object_id in after_names and after_names[object_id] != old_name
    }


def _segment(value):
    output = []; capitalize = True
    for char in str(value or "").strip():
        if char.isalnum():
            output.append(char.upper() if capitalize else char); capitalize = False
        else:
            capitalize = True
    return "".join(output) or "Object"
