from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.authoring.identity_editor import edit_identity, edit_locator
from automation_harness.authoring.object_identity_workbench import open_capture_workbench
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService
from automation_harness.core.repository_merge import component_diff, definitions_equal


def _install_capture_backend(app_module):
    """Install the RHEL desktop capture service before AuthoringApp starts.

    Native GTK/Swing applications remain on AT-SPI. Instrumented JavaFX JVMs
    are captured through the JavaFX scene-graph bridge because Linux OpenJFX
    does not expose those Nodes through AT-SPI.
    """
    app_module.ObjectCaptureService = HybridObjectCaptureService


def _install_capture_next_click(app_module):
    """Capture the next desktop click without intercepting the pointer in GTK."""

    def capture_next_click(self):
        if not self.capture.available:
            return self._error(
                "Capture unavailable",
                "Neither AT-SPI nor an instrumented JavaFX bridge is available on this host.",
            )
        if self._click_capture_active:
            return
        self._click_capture_active = True
        self._set_status("Waiting for the next desktop click…")
        self.window.hide()

        def worker():
            try:
                captured = self.capture.capture_next_click(timeout=30.0)
            except Exception as exc:
                GLib.idle_add(self._finish_next_click_capture, None, exc)
            else:
                GLib.idle_add(self._finish_next_click_capture, captured, None)

        threading.Thread(
            target=worker,
            name="automation-desktop-click-capture",
            daemon=True,
        ).start()

    app_module.AuthoringApp.capture_next_click = capture_next_click


def _install_repository_workspace(app_module):
    """Give Object Capture a disposable in-memory repository workspace.

    Capture no longer requires a repository path up front. A capture is first
    inspected as live evidence, then explicitly added to the in-memory working
    repository, and the repository itself is saved or merged only when the
    author chooses to do so.
    """
    original_init = app_module.AuthoringApp.__init__
    original_build = app_module.AuthoringApp._build
    original_build_objects = app_module.AuthoringApp._build_objects
    original_open_repository = app_module.AuthoringApp.open_repository

    def init(self, repository_path=None, mode="author"):
        # Object Capture always starts from a clean workspace. An existing
        # repository can subsequently be opened or merged explicitly.
        initial_path = None if mode == "capture" else repository_path
        original_init(self, initial_path, mode=mode)
        self._repository_dirty = False
        self._pending_capture = None
        self._capture_workbench = None
        if mode == "capture":
            self.repository_path = None
            self.repository = ComponentRepository({})
            self.refresh_objects()
            self._set_status("New empty repository — capture an object to begin")
            self._update_repository_title()

    def build(self):
        original_build(self)
        if self.mode not in {"capture", "repository"}:
            return
        outer = self.window.get_child()
        if outer is None or not hasattr(outer, "get_children"):
            return
        children = outer.get_children()
        if not children:
            return
        toolbar = children[0]
        self._button(toolbar, "Save Repository", self.save_repository)
        self._button(toolbar, "Save Repository As", self.save_repository_as)
        self._button(toolbar, "Merge Repository", self.merge_repository)

    def build_objects(self):
        # The compound Object Identity Workbench owns post-capture naming,
        # identity editing, highlighting, and save actions.  Keep the main
        # Object Capture tab focused on repository navigation and capture.
        original_build_objects(self)

    def set_pending_capture_controls(self, enabled):
        # Compatibility hook retained for older capture paths.  The workbench
        # no longer requires a separate Add Capture / Discard Capture row.
        return None

    def mark_repository_dirty(self, dirty=True):
        self._repository_dirty = bool(dirty)
        self._update_repository_title()

    def update_repository_title(self):
        base = {
            "capture": "Automation Harness Object Capture",
            "repository": "Automation Harness Object Repository",
        }.get(self.mode, "Automation Harness Author")
        path = getattr(self, "repository_path", None)
        if self.mode in {"capture", "repository"}:
            suffix = path.name if path is not None else "Untitled Repository"
            dirty = " *" if getattr(self, "_repository_dirty", False) else ""
            self.window.set_title("%s — %s%s" % (base, suffix, dirty))

    def save_repository(self):
        path = getattr(self, "repository_path", None)
        if path is None:
            return self.save_repository_as()
        try:
            self.repository.save(path)
        except Exception as exc:
            return self._error("Repository save failed", "%s: %s" % (type(exc).__name__, exc))
        self._mark_repository_dirty(False)
        self._set_status("Saved repository: " + str(path))
        return path

    def save_repository_as(self):
        filename = self._choose_file(save=True, yaml=True)
        if not filename:
            return None
        path = Path(filename)
        try:
            self.repository.save(path)
        except Exception as exc:
            return self._error("Repository save failed", "%s: %s" % (type(exc).__name__, exc))
        self.repository_path = path
        self._mark_repository_dirty(False)
        self._set_status("Saved repository: " + str(path))
        return path

    def open_repository(self):
        if self.mode not in {"capture", "repository"}:
            return original_open_repository(self)
        filename = self._choose_file(yaml=True)
        if not filename:
            return
        path = Path(filename)
        if getattr(self, "_repository_dirty", False):
            if not self._confirm(
                "Replace unsaved repository?",
                "Opening a repository replaces the current in-memory workspace. Continue?",
            ):
                return
        try:
            repository = ComponentRepository.load([path])
        except Exception as exc:
            return self._error("Repository error", "%s: %s" % (type(exc).__name__, exc))
        self.repository = repository
        self.repository_path = path
        self._mark_repository_dirty(False)
        self.refresh_objects()
        self._set_status("Opened repository: " + str(path))

    def merge_repository(self):
        filename = self._choose_file(yaml=True)
        if not filename:
            return
        path = Path(filename)
        try:
            incoming = ComponentRepository.load([path])
        except Exception as exc:
            return self._error("Repository merge failed", "%s: %s" % (type(exc).__name__, exc))

        merged = dict(self.repository.components)
        added = 0
        replaced = 0
        identical = 0
        for component_id, incoming_definition in sorted(incoming.components.items()):
            current = merged.get(component_id)
            if current is None:
                merged[component_id] = incoming_definition
                added += 1
                continue
            if definitions_equal(component_id, current, incoming_definition):
                identical += 1
                continue
            decision = _merge_conflict_dialog(self, component_id, current, incoming_definition)
            if decision == "cancel":
                self._set_status("Repository merge cancelled")
                return
            if decision == "incoming":
                merged[component_id] = incoming_definition
                replaced += 1

        changed = added + replaced
        if changed:
            self.repository = ComponentRepository(merged)
            self._mark_repository_dirty(True)
            self.refresh_objects()
        self._set_status(
            "Merged %s: %d added, %d replaced, %d identical"
            % (path.name, added, replaced, identical)
        )

    app_module.AuthoringApp.__init__ = init
    app_module.AuthoringApp._build = build
    app_module.AuthoringApp._build_objects = build_objects
    app_module.AuthoringApp._set_pending_capture_controls = set_pending_capture_controls
    app_module.AuthoringApp._mark_repository_dirty = mark_repository_dirty
    app_module.AuthoringApp._update_repository_title = update_repository_title
    app_module.AuthoringApp.save_repository = save_repository
    app_module.AuthoringApp.save_repository_as = save_repository_as
    app_module.AuthoringApp.open_repository = open_repository
    app_module.AuthoringApp.merge_repository = merge_repository


def _install_javafx_authoring(app_module):
    """Make the compound identity workbench the primary post-capture surface."""

    def present_capture(self, captured):
        self._last_capture = captured
        self._pending_capture = captured
        if hasattr(self, "highlight_button"):
            self.highlight_button.set_sensitive(bool(captured.bounds))

        strategy = captured.candidate_strategy()
        if strategy.type == "javafx":
            candidate = strategy.options.get("identification")
        elif strategy.type == "anchored_visual":
            candidate = {"strategy": "anchored_visual"}
        else:
            candidate = captured.candidate_identification().to_dict()

        try:
            assessments = [item.to_dict() for item in self.capture.assess(captured)]
        except Exception as exc:
            assessments = [{"error": "%s: %s" % (type(exc).__name__, exc)}]

        # Retain a compact diagnostic snapshot in the main capture tab, while
        # all authoring interaction occurs in the structured workbench.
        self._set_text(
            self.object_detail,
            json.dumps(
                {
                    "capture": captured.to_dict(),
                    "proposed_identity": candidate,
                    "locator_assessments": assessments,
                },
                indent=2,
                default=str,
            ),
        )
        open_capture_workbench(self, captured)
        self._set_status("Captured object — Object Identity Workbench opened")

    def add_pending_capture(self):
        # Legacy command kept callable for compatibility, but normal capture no
        # longer routes through a separate naming/identity dialog sequence.
        captured = getattr(self, "_pending_capture", None)
        if captured is None:
            return self._info("Object Capture", "Capture an object first.")
        return open_capture_workbench(self, captured)

    def discard_pending_capture(self):
        self._pending_capture = None
        workbench = getattr(self, "_capture_workbench", None)
        if workbench is not None:
            try:
                workbench.window.destroy()
            except Exception:
                pass
        self._set_text(self.object_detail, "")
        self._set_status("Capture discarded")

    def capture_by_locator(self):
        if not self.capture.available:
            return self._error(
                "Capture unavailable",
                "Neither AT-SPI nor an instrumented JavaFX bridge is available on this host.",
            )
        values = edit_locator(self.window, ("name", "role", "accessible_id"))
        if values is None:
            return
        try:
            captured = self.capture.capture_by_locator(
                name=values.get("name") or None,
                role=values.get("role") or None,
                accessible_id=values.get("accessible_id") or None,
            )
            self._present_capture(captured)
        except Exception as exc:
            self._error("Capture failed", "%s: %s" % (type(exc).__name__, exc))

    def resolve_selected_for_highlight(self, definition):
        deadline = time.monotonic() + 5.0
        last_error = None
        while time.monotonic() < deadline:
            for strategy in definition.strategies:
                try:
                    if strategy.type == "anchored_visual":
                        captured = self.capture.resolve_anchored_visual(strategy.options)
                    elif strategy.type in {"atspi", "java_accessibility"}:
                        captured = self.capture.capture_by_locator(
                            identification=strategy.options.get("identification")
                        )
                    elif strategy.type == "javafx":
                        captured = self.capture.javafx_driver.inspect(
                            identification=strategy.options.get("identification")
                        )
                    else:
                        continue
                    GLib.idle_add(self._finish_repository_highlight, captured, None)
                    return
                except Exception as exc:
                    last_error = exc
            time.sleep(0.2)
        GLib.idle_add(
            self._finish_repository_highlight,
            None,
            LookupError(
                "No live object matched %r within 5 seconds. %s"
                % (definition.component_id, last_error or "")
            ),
        )

    app_module.AuthoringApp._present_capture = present_capture
    app_module.AuthoringApp.add_pending_capture = add_pending_capture
    app_module.AuthoringApp.discard_pending_capture = discard_pending_capture
    app_module.AuthoringApp.capture_by_locator = capture_by_locator
    app_module.AuthoringApp._resolve_selected_for_highlight = resolve_selected_for_highlight


def _merge_conflict_dialog(app, component_id, current, incoming):
    diff = component_diff(component_id, current, incoming)
    dialog = Gtk.Dialog(
        title="Merge conflict: %s" % component_id,
        transient_for=app.window,
        flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
    )
    dialog.add_buttons(
        "Cancel Merge", Gtk.ResponseType.CANCEL,
        "Keep Current", Gtk.ResponseType.REJECT,
        "Use Incoming", Gtk.ResponseType.ACCEPT,
    )
    dialog.set_default_size(840, 560)
    box = dialog.get_content_area()
    box.set_spacing(8)
    box.set_border_width(10)
    label = Gtk.Label(label="Both repositories define %s differently." % component_id)
    label.set_halign(Gtk.Align.START)
    box.pack_start(label, False, False, 0)
    view = Gtk.TextView()
    view.set_editable(False)
    view.set_monospace(True)
    view.get_buffer().set_text(diff or "Definitions differ.")
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.add(view)
    box.pack_start(scroll, True, True, 0)
    dialog.show_all()
    response = dialog.run()
    dialog.destroy()
    if response == Gtk.ResponseType.ACCEPT:
        return "incoming"
    if response == Gtk.ResponseType.REJECT:
        return "current"
    return "cancel"


def _install(app_module):
    _install_capture_backend(app_module)
    _install_capture_next_click(app_module)
    _install_javafx_authoring(app_module)
    _install_repository_workspace(app_module)


def run_author(argv=None):
    from automation_harness.authoring import app

    _install(app)
    return app.main(argv)


def run_capture(argv=None):
    from automation_harness.authoring import app

    _install(app)
    return app.capture_main(argv)


def run_repository(argv=None):
    from automation_harness.authoring import app

    _install(app)
    return app.repository_main(argv)
