from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import cairo  # noqa: F401
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.authoring.action_catalog import action_by_id, actions_for
from automation_harness.authoring.project import AuthoringProject, create_authoring_project
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_steps import ReusableStepDefinition, list_reusable_steps
from automation_harness.core.object_capture import ObjectCaptureService
from automation_harness.core.script_steps import ScriptStepDefinition, registered_script_step
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import derive_execution_state, embed_plan_repository, load_plan, repository_from_plan, save_plan, validate_plan, validate_plan_components
from automation_harness.core.visual_baselines import approve_visual_candidate, reject_visual_candidate
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan
from automation_harness.recording import RecordedInteraction, RecordingSession, RepositoryMatch, interactions_to_steps
from automation_harness.recording.adapters.javafx import JavaFxRecordingAdapter
from automation_harness.recording.adapters.atspi import AtspiRecordingAdapter
from automation_harness.drivers.javafx_bridge import HttpJavaFxBridgeTransport
from automation_harness.runner.plan_execution import execute_plan
from automation_harness.formats import PLAN_SUFFIX, PROJECT_SUFFIX, REPOSITORY_SUFFIX, artifact_stem, with_artifact_suffix


class AuthoringApp:
    """GTK3 authoring/Object Capture client for the RHEL deployment environment."""

    def __init__(self, repository_path: Path | None = None, *, mode: str = "author", project_path: Path | None = None, recording_session_factory=None) -> None:
        self.mode = mode
        self.project_path = project_path
        self.plan_path = None
        self.project = AuthoringProject.load(project_path) if project_path is not None else None
        if self.project is not None:
            repository_path = self.project.repository
        self.window = Gtk.Window(title={
            "capture": "Automation Harness Object Capture",
            "repository": "Automation Harness Object Repository",
        }.get(mode, "Automation Harness Author"))
        self.window.set_default_size(1180, 760)
        self.window.connect("destroy", self._on_destroy)
        self.registry = default_step_registry()
        self.capture = ObjectCaptureService()
        self.repository_path = repository_path
        self.repository = self._load_repository()
        self.plan = TestPlan(name="new-test-plan")
        self.selected_action = None
        self._run_active = False
        self._click_capture_active = False
        self._click_picker = None
        self._click_picker_timeout = None
        self._last_capture = None
        self._highlight_windows = []
        self._highlight_timeout = None
        self.last_run_dir = None
        self.recording_session_factory = recording_session_factory
        self.recording_session: RecordingSession | None = None
        self.recording_stop_window = None
        self.recording_stop_button = None
        self._recording_stop_active = False
        self._recorded_object_save_active = False
        self.recorded_interactions: list[RecordedInteraction] = []
        self._build()
        self.window.connect("key-press-event", self._on_key_press)
        self.refresh_all()
        self.window.show_all()

    def _on_destroy(self, *_args) -> None:
        Gtk.main_quit()

    def _load_repository(self) -> ComponentRepository:
        if self.repository_path is None:
            return ComponentRepository({})
        return ComponentRepository.load([self.repository_path])

    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(6)
        self.window.add(outer)

        toolbar = Gtk.Box(spacing=6)
        outer.pack_start(toolbar, False, False, 0)
        self._button(toolbar, "Open Repository", self.open_repository)
        if self.mode == "author":
            self._button(toolbar, "New Project", self.new_project_dialog)
            self._button(toolbar, "Open Project", self.open_project_dialog)
            self._button(toolbar, "Open Plan", self.open_plan_dialog)
            self._button(toolbar, "Save Plan", self.save_plan_dialog)
            self._button(toolbar, "Save Plan As", self.save_plan_as_dialog)
            self._button(toolbar, "Validate Plan", self.validate_plan_dialog)
            self.run_reference_button = self._button(toolbar, "Run Test", self.run_reference_plan)
            self._button(toolbar, "Add Script Step", self.add_script_step_dialog)
            self._button(toolbar, "Save as Reusable Step", self.save_reusable_step)
            count = len(list_reusable_steps(self.project.root / "reusable_steps")) if self.project else 0
            if count:
                toolbar.pack_start(Gtk.Label(label="Reusable steps: %d" % count), False, False, 0)
        self.status = Gtk.Label(label="Ready")
        self.status.set_halign(Gtk.Align.END)
        toolbar.pack_end(self.status, True, True, 0)

        notebook = Gtk.Notebook()
        outer.pack_start(notebook, True, True, 0)
        self.notebook = notebook
        self.objects_tab = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        notebook.append_page(self.objects_tab, Gtk.Label(label="Object Repository"))
        self._build_objects()
        if self.mode == "capture" or self.mode == "repository":
            return
        self.steps_tab = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.plan_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.vars_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.state_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        notebook.append_page(self.steps_tab, Gtk.Label(label="Actions"))
        notebook.append_page(self.plan_tab, Gtk.Label(label="Test Flow"))
        notebook.append_page(self.vars_tab, Gtk.Label(label="Variables"))
        notebook.append_page(self.state_tab, Gtk.Label(label="Run State"))
        self._build_steps()
        self._build_plan()
        self._build_variables()
        self._build_state()

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

    @staticmethod
    def _tree(columns):
        store = Gtk.ListStore(*([str] * len(columns)))
        tree = Gtk.TreeView(model=store)
        for index, (title, width) in enumerate(columns):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_min_width(width)
            tree.append_column(column)
        return tree, store

    def _build_objects(self) -> None:
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.objects_tab.pack1(left, resize=True, shrink=False)
        filter_row = Gtk.Box(spacing=6)
        left.pack_start(filter_row, False, False, 0)
        filter_row.pack_start(Gtk.Label(label="Filter:"), False, False, 0)
        self.object_filter = Gtk.SearchEntry()
        self.object_filter.set_placeholder_text("ID, description, type, framework, or class")
        self.object_filter.connect("search-changed", lambda *_args: self.refresh_objects())
        filter_row.pack_start(self.object_filter, True, True, 0)
        self.object_count = Gtk.Label(label="0 objects")
        filter_row.pack_end(self.object_count, False, False, 0)
        self.object_tree, self.object_store = self._tree((("Component", 260), ("Rev", 50), ("Type", 110), ("Actions", 180)))
        self.object_tree.get_selection().connect("changed", lambda *_args: self._object_selection_changed())
        left.pack_start(self._scrolled(self.object_tree), True, True, 0)
        buttons = Gtk.Box(spacing=5)
        left.pack_start(buttons, False, False, 0)
        self._button(buttons, "Inspect", self.show_object)
        self._button(buttons, "Highlight", self.highlight_selected_object)
        if self.mode != "capture":
            self._button(buttons, "Edit Selected", self.edit_selected_object)
            self._button(buttons, "Remove Selected", self.remove_selected_object)
        if self.mode == "author":
            self._button(buttons, "Click Selected", self.click_selected_object)
        if self.mode != "repository":
            capture_buttons = Gtk.Box(spacing=5)
            left.pack_start(capture_buttons, False, False, 0)
            self._button(capture_buttons, "Capture Next Click", self.capture_next_click)
            self._button(capture_buttons, "Capture at Pointer (2s)", self.capture_pointer_delayed)
            self._button(capture_buttons, "Capture by Locator", self.capture_by_locator)
            visual_buttons = Gtk.Box(spacing=5)
            left.pack_start(visual_buttons, False, False, 0)
            self.highlight_button = self._button(visual_buttons, "Highlight Last Capture", self.highlight_last_capture)
            self.highlight_button.set_sensitive(False)
            self._button(visual_buttons, "Approve Visual", self.approve_visual_candidate)
            self._button(visual_buttons, "Reject Visual", self.reject_visual_candidate)

        self.object_detail = Gtk.TextView()
        self.object_detail.set_editable(False)
        self.object_detail.set_monospace(True)
        self.objects_tab.pack2(self._scrolled(self.object_detail), resize=True, shrink=False)
        self.objects_tab.set_position(620)

    def _build_steps(self) -> None:
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.steps_tab.pack1(left, resize=True, shrink=False)
        filter_row = Gtk.Box(spacing=6)
        left.pack_start(filter_row, False, False, 0)
        filter_row.pack_start(Gtk.Label(label="Filter:"), False, False, 0)
        self.step_filter = Gtk.SearchEntry()
        self.step_filter.set_placeholder_text("Action name, category, or description")
        self.step_filter.connect("search-changed", lambda *_args: self.refresh_steps())
        filter_row.pack_start(self.step_filter, True, True, 0)
        self.step_count = Gtk.Label(label="Select an object")
        filter_row.pack_end(self.step_count, False, False, 0)
        self.step_tree, self.step_store = self._tree((("Action", 200), ("ID", 150), ("Category", 130), ("Description", 400)))
        self.step_tree.get_selection().connect("changed", lambda *_args: self.show_step())
        self.step_tree.connect("row-activated", lambda *_args: self.add_selected_step())
        left.pack_start(self._scrolled(self.step_tree), True, True, 0)
        self._button(left, "Add Action as Step", self.add_selected_step)
        self.step_detail = Gtk.TextView()
        self.step_detail.set_editable(False)
        self.step_detail.set_monospace(True)
        self.steps_tab.pack2(self._scrolled(self.step_detail), resize=True, shrink=False)
        self.steps_tab.set_position(690)

    def _build_plan(self) -> None:
        top = Gtk.Box(spacing=6)
        self.plan_tab.pack_start(top, False, False, 0)
        top.pack_start(Gtk.Label(label="Plan name:"), False, False, 0)
        self.plan_name = Gtk.Entry()
        self.plan_name.set_text(self.plan.name)
        top.pack_start(self.plan_name, False, False, 0)
        self._button(top, "Edit Selected", self.edit_plan_step)
        self._button(top, "Duplicate", self.duplicate_plan_step)
        self._button(top, "Move Up", lambda: self.move_plan_step(-1))
        self._button(top, "Move Down", lambda: self.move_plan_step(1))
        self._button(top, "Remove Selected", self.remove_plan_step)
        self.stop_recording_button = self._button(top, "Stop Recording", self.stop_recording)
        self.stop_recording_button.set_sensitive(False)
        self.start_recording_button = self._button(top, "Start Recording", self.start_recording)
        self.plan_tree, self.plan_store = self._tree((("Node", 100), ("Registered Step", 260), ("Inputs", 300), ("Outputs", 220), ("Depends", 140)))
        self.plan_tree.connect("row-activated", lambda *_args: self.edit_plan_step())
        self.plan_tree.connect("key-press-event", self._on_plan_tree_key_press)
        self.plan_tab.pack_start(self._scrolled(self.plan_tree), True, True, 0)
        recording = Gtk.Box(spacing=6)
        self.plan_tab.pack_start(recording, False, False, 0)
        self.recording_tree, self.recording_store = self._tree((("#", 40), ("Action", 110), ("Semantic Target", 230), ("Parameters", 180), ("Repository Match", 170), ("Confidence", 90)))
        recording.pack_start(self._scrolled(self.recording_tree), True, True, 0)
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        recording.pack_start(buttons, False, False, 0)
        self._button(buttons, "Keep", self.keep_recorded_interaction)
        self._button(buttons, "Delete", self.delete_recorded_interaction)
        self._button(buttons, "Add Selected as Step", self.add_recorded_interaction_to_test)

    def _on_key_press(self, _widget, event):
        control = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if not control:
            return False
        key = (Gdk.keyval_name(event.keyval) or "").lower()
        if key == "f":
            page = self.notebook.get_current_page()
            if page == 0:
                self.object_filter.grab_focus()
            elif self.mode == "author" and page == 1:
                self.step_filter.grab_focus()
            return True
        if self.mode == "author" and key == "o":
            self.open_plan_dialog(); return True
        if self.mode == "author" and key == "s":
            self.save_plan_dialog(); return True
        return False

    def _on_plan_tree_key_press(self, _widget, event):
        if event.keyval in (Gdk.KEY_Delete, Gdk.KEY_BackSpace):
            self.remove_plan_step(); return True
        return False

    def _build_variables(self) -> None:
        self.vars_tab.pack_start(Gtk.Label(label="Plan globals (JSON object)"), False, False, 0)
        self.variables_text = Gtk.TextView()
        self.variables_text.set_monospace(True)
        self.vars_tab.pack_start(self._scrolled(self.variables_text), True, True, 0)
        self._button(self.vars_tab, "Apply Variables", self.apply_variables)

    def _build_state(self) -> None:
        self.state_caption = Gtk.Label(label="Pre-execution managed queue projection")
        self.state_caption.set_halign(Gtk.Align.START)
        self.state_tab.pack_start(self.state_caption, False, False, 0)
        self.state_tree, self.state_store = self._tree((("Node", 100), ("Step", 300), ("Status", 100), ("Unresolved Variables", 420)))
        self.state_tab.pack_start(self._scrolled(self.state_tree), True, True, 0)
        self._button(self.state_tab, "Refresh Projection", self.refresh_state)

    def _selected(self, tree, column=0):
        model, iterator = tree.get_selection().get_selected()
        return model.get_value(iterator, column) if iterator is not None else None

    @staticmethod
    def _select_value(tree, value, column=0):
        if value is None:
            return
        model = tree.get_model()
        iterator = model.get_iter_first()
        while iterator is not None:
            if model.get_value(iterator, column) == value:
                tree.get_selection().select_iter(iterator)
                tree.scroll_to_cell(model.get_path(iterator))
                return
            iterator = model.iter_next(iterator)

    def _set_status(self, value):
        self.status.set_text(value)

    @staticmethod
    def _set_text(widget, value):
        widget.get_buffer().set_text(value)

    @staticmethod
    def _get_text(widget):
        buffer = widget.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)

    def refresh_all(self) -> None:
        self.refresh_objects()
        if self.mode != "author":
            return
        self.refresh_steps(); self.refresh_plan(); self.refresh_variables(); self.refresh_state()

    def refresh_objects(self) -> None:
        selected = self._selected(self.object_tree)
        self.object_store.clear()
        query = self.object_filter.get_text().strip().casefold()
        visible = 0
        for component_id, definition in sorted(self.repository.components.items()):
            searchable = " ".join((component_id, definition.description, definition.object_type.value, definition.framework or "", definition.native_class or "")).casefold()
            if query and query not in searchable:
                continue
            self.object_store.append((component_id, str(definition.revision), definition.object_type.value, ",".join(sorted(item.value for item in definition.semantic_actions))))
            visible += 1
        total = len(self.repository.components)
        self.object_count.set_text("%d of %d" % (visible, total) if query else "%d objects" % total)
        self._select_value(self.object_tree, selected)

    def show_object(self) -> None:
        component_id = self._selected(self.object_tree)
        if not component_id:
            return
        definition = self.repository.get(component_id)
        payload = {
            "component_id": definition.component_id,
            "description": definition.description,
            "revision": definition.revision,
            "actions": sorted(definition.actions),
            "semantic_actions": sorted(item.value for item in definition.semantic_actions),
            "object_type": definition.object_type.value,
            "framework": definition.framework,
            "native_class": definition.native_class,
            "properties": dict(definition.properties),
            "subobjects": {key: dict(value) for key, value in definition.subobjects.items()},
            "expected_states": dict(definition.expected_states),
            "visual": dict(definition.visual) if definition.visual else None,
            "strategies": [{"type": item.type, **item.options} for item in definition.strategies],
        }
        self._set_text(self.object_detail, json.dumps(payload, indent=2, default=str))

    def remove_selected_object(self) -> None:
        component_id = self._selected(self.object_tree)
        if not component_id:
            return self._info("Object Repository", "Select an object first.")
        if not self._confirm("Remove object", "Remove %s from the current repository?" % component_id):
            return
        components = dict(self.repository.components)
        components.pop(component_id, None)
        self.repository = ComponentRepository(components)
        if self.mode in {"capture", "repository"} and hasattr(self, "_mark_repository_dirty"):
            self._mark_repository_dirty(True)
        elif self.repository_path is not None:
            self.repository.save(self.repository_path)
        self.refresh_objects()
        self._set_text(self.object_detail, "")
        self._set_status("Removed object: " + component_id)

    def _object_selection_changed(self) -> None:
        self.show_object()
        if self.mode == "author":
            self.refresh_steps()

    def edit_selected_object(self) -> None:
        component_id = self._selected(self.object_tree)
        if not component_id:
            return self._info("Object Repository", "Select a component to edit.")
        if self.repository_path is None:
            return self._error("Object Repository", "Open or create an editable repository first.")
        definition = self.repository.get(component_id)
        document = ComponentRepository({component_id: definition}).to_document()["components"][component_id]
        raw = self._ask_text("Edit component", "Component definition JSON:", json.dumps(document, indent=2), multiline=True)
        if raw is None:
            return
        try:
            value = json.loads(raw)
            parsed = ComponentRepository.from_document({"version": 1, "components": {component_id: value}}, source="editor")
            editable = ComponentRepository.load([self.repository_path]) if self.repository_path.exists() else ComponentRepository({})
            editable.with_component(parsed.get(component_id)).save(self.repository_path)
            self.repository = self._load_repository(); self.refresh_objects(); self._set_status("Saved " + component_id)
        except Exception as exc:
            self._error("Object Repository", "%s: %s" % (type(exc).__name__, exc))

    def capture_pointer_delayed(self) -> None:
        if not self.capture.available:
            return self._error("AT-SPI unavailable", "pyatspi is not installed on this host.")
        self._set_status("Move pointer over object…")
        GLib.timeout_add(2000, self._capture_pointer_now)

    def _capture_pointer_now(self):
        try:
            display = Gdk.Display.get_default()
            seat = display.get_default_seat()
            pointer = seat.get_pointer()
            _screen, x, y = pointer.get_position()
            captured = self.capture.capture_at_point(int(x), int(y))
            self._present_capture(captured)
        except Exception as exc:
            self._error("Capture failed", "%s: %s" % (type(exc).__name__, exc))
        self._set_status("Ready")
        return False

    def capture_next_click(self) -> None:
        if not self.capture.available:
            return self._error("AT-SPI unavailable", "pyatspi is not installed on this host.")
        if self._click_capture_active:
            return
        self._click_capture_active = True
        self._set_status("Click the object within 30 seconds…")
        self.window.hide()
        GLib.timeout_add(150, self._show_click_picker)

    def _show_click_picker(self):
        if not self._click_capture_active:
            return False
        picker = Gtk.Window(type=Gtk.WindowType.POPUP)
        picker.set_decorated(False); picker.set_keep_above(True); picker.set_opacity(0.01)
        picker.add_events(Gdk.EventMask.BUTTON_RELEASE_MASK | Gdk.EventMask.KEY_PRESS_MASK)
        picker.connect("button-release-event", self._click_picker_selected)
        picker.connect("key-press-event", self._click_picker_key)
        picker.fullscreen(); picker.show_all(); picker.present()
        try:
            gdk_window = picker.get_window()
            if gdk_window:
                cursor = Gdk.Cursor.new_from_name(Gdk.Display.get_default(), "crosshair")
                gdk_window.set_cursor(cursor)
        except Exception:
            pass
        self._click_picker = picker
        self._click_picker_timeout = GLib.timeout_add_seconds(30, self._click_picker_timed_out)
        return False

    def _click_picker_selected(self, _widget, event):
        point = (int(event.x_root), int(event.y_root))
        self._destroy_click_picker()
        GLib.timeout_add(120, lambda: (self._capture_click_point(point), False)[1])
        return True

    def _click_picker_key(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self._destroy_click_picker(); self._finish_next_click_capture(error=RuntimeError("capture cancelled")); return True
        return False

    def _click_picker_timed_out(self):
        self._destroy_click_picker(); self._finish_next_click_capture(error=TimeoutError("capture timed out")); return False

    def _destroy_click_picker(self):
        if self._click_picker_timeout is not None:
            GLib.source_remove(self._click_picker_timeout); self._click_picker_timeout = None
        if self._click_picker is not None:
            self._click_picker.destroy(); self._click_picker = None

    def _capture_click_point(self, point):
        threading.Thread(target=self._resolve_click_point, args=point, name="automation-object-capture", daemon=True).start()

    def _resolve_click_point(self, x, y):
        try:
            captured = self.capture.capture_scoped_at_point(x, y)
        except Exception as exc:
            GLib.idle_add(self._finish_next_click_capture, None, exc)
        else:
            GLib.idle_add(self._finish_next_click_capture, captured, None)

    def _finish_next_click_capture(self, captured=None, error=None):
        self._click_capture_active = False
        if error is not None:
            self.window.show_all(); self.window.present(); self._set_status("Ready")
            self._error("Capture failed", "%s: %s" % (type(error).__name__, error)); return False
        self._set_status("Captured clicked object")
        self._show_highlight_then_present(captured)
        return False

    def capture_by_locator(self) -> None:
        if not self.capture.available:
            return self._error("AT-SPI unavailable", "pyatspi is not installed on this host.")
        raw = self._ask_text("Capture by locator", "Locator JSON (name, role, accessible_id):", '{"name":"","role":"","accessible_id":""}', multiline=True)
        if raw is None:
            return
        try:
            values = json.loads(raw)
            captured = self.capture.capture_by_locator(name=values.get("name") or None, role=values.get("role") or None, accessible_id=values.get("accessible_id") or None)
            self._present_capture(captured)
        except Exception as exc:
            self._error("Capture failed", "%s: %s" % (type(exc).__name__, exc))

    def _present_capture(self, captured) -> None:
        self._last_capture = captured
        if hasattr(self, "highlight_button"):
            self.highlight_button.set_sensitive(bool(captured.bounds))
        assessments = [item.to_dict() for item in self.capture.assess(captured)]
        self._set_text(self.object_detail, json.dumps({"capture": captured.to_dict(), "locator_assessments": assessments}, indent=2, default=str))
        if self.repository_path is None:
            if not self._confirm("Save capture", "No editable repository is open. Choose a repository file now?"):
                return
            path = self._choose_file(save=True, yaml=True, artifact_suffix=REPOSITORY_SUFFIX, title="Save Object Repository")
            if not path:
                return
            self.repository_path = Path(path)
        component_id = self._ask_text("Save capture", "Logical component ID:")
        if not component_id:
            return
        try:
            if captured.candidate_strategy().type == "anchored_visual":
                definition = self.capture.save_capture(self.repository_path, component_id, captured)
            else:
                candidate = captured.candidate_identification().to_dict()
                identity_raw = self._ask_text("Object identification", "AT-SPI identity JSON:", json.dumps(candidate, separators=(",", ":")), multiline=True)
                if identity_raw is None:
                    return
                identification = json.loads(identity_raw)
                if not isinstance(identification, dict):
                    raise ValueError("identification must be a JSON object")
                definition = self.capture.save_capture(self.repository_path, component_id, captured, identification=identification)
        except Exception as exc:
            return self._error("Object identification", "%s: %s" % (type(exc).__name__, exc))
        self.repository = self._load_repository(); self.refresh_objects(); self._set_status("Saved %s revision %s" % (definition.component_id, definition.revision))
        if captured.bounds and self._confirm("Visual capture", "Stage a component-bounds visual candidate now?"):
            try:
                result = self.capture.stage_visual_capture(self.repository_path, component_id, captured)
                self._set_status("Staged visual candidate: " + result["variant_key"])
                self._info("Visual candidate", json.dumps(result, indent=2, default=str))
            except Exception as exc:
                self._error("Visual capture", "%s: %s" % (type(exc).__name__, exc))

    def bind_captured_application(self, _captured) -> bool:
        # Application/window ownership remains in the captured object's locator.
        return True

    def highlight_last_capture(self) -> None:
        if self._last_capture is None or self._last_capture.bounds is None:
            return self._info("Highlight capture", "Capture an object with screen bounds first.")
        self.window.hide(); self._show_highlight(self._last_capture.bounds, True)

    def _show_highlight_then_present(self, captured) -> None:
        self._last_capture = captured
        if not captured.bounds:
            self.window.show_all(); self.window.present(); return self._present_capture(captured)
        self.window.hide(); self._show_highlight(captured.bounds, False)
        self._highlight_timeout = GLib.timeout_add(1600, self._restore_after_highlight, captured)

    def _show_highlight(self, bounds, restore_editor):
        self._clear_highlight()
        provider = Gtk.CssProvider(); provider.load_from_data(b"* { background-color: #ff3b30; }")
        for x, y, width, height in _highlight_rectangles(bounds):
            edge = Gtk.Window(type=Gtk.WindowType.POPUP); edge.set_decorated(False); edge.set_keep_above(True); edge.set_opacity(0.88)
            edge.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            edge.move(x, y); edge.resize(width, height); edge.show_all(); self._highlight_windows.append(edge)
        if restore_editor:
            self._highlight_timeout = GLib.timeout_add(1600, self._restore_after_highlight, None)

    def _restore_after_highlight(self, captured=None):
        self._clear_highlight(); self.window.show_all(); self.window.present()
        if captured is not None:
            self._present_capture(captured)
        return False

    def _clear_highlight(self):
        if self._highlight_timeout is not None:
            try: GLib.source_remove(self._highlight_timeout)
            except Exception: pass
            self._highlight_timeout = None
        for edge in self._highlight_windows:
            edge.destroy()
        self._highlight_windows = []

    def highlight_selected_object(self) -> None:
        component_id = self._selected(self.object_tree)
        if not component_id:
            return
        if not self.capture.available:
            return self._error("Highlight", "pyatspi is not installed on this host.")
        definition = self.repository.get(component_id); self._set_status("Resolving %s for highlight…" % component_id)
        threading.Thread(target=self._resolve_selected_for_highlight, args=(definition,), daemon=True).start()

    def _resolve_selected_for_highlight(self, definition):
        deadline = time.monotonic() + 5.0; last_error = None
        while time.monotonic() < deadline:
            for strategy in definition.strategies:
                try:
                    if strategy.type == "anchored_visual": captured = self.capture.resolve_anchored_visual(strategy.options)
                    elif strategy.type in {"atspi", "java_accessibility"}: captured = self.capture.capture_by_locator(identification=strategy.options.get("identification"))
                    else: continue
                    GLib.idle_add(self._finish_repository_highlight, captured, None); return
                except Exception as exc: last_error = exc
            time.sleep(0.2)
        GLib.idle_add(self._finish_repository_highlight, None, LookupError("No live object matched %r within 5 seconds. %s" % (definition.component_id, last_error or "")))

    def _finish_repository_highlight(self, captured=None, error=None):
        if error is not None:
            self._set_status("Ready"); self._error("Highlight failed", str(error)); return False
        self._last_capture = captured
        if hasattr(self, "highlight_button"): self.highlight_button.set_sensitive(bool(captured.bounds))
        if captured.bounds is None:
            return self._error("Highlight failed", "The resolved object has no screen bounds to highlight.")
        self.window.hide(); self._show_highlight(captured.bounds, True); return False

    def click_selected_object(self) -> None:
        component_id = self._selected(self.object_tree)
        if not component_id:
            return self._info("Click", "Select a captured object first.")
        definition = self.repository.get(component_id)
        if not any(strategy.type in {"atspi", "java_accessibility", "javafx"} for strategy in definition.strategies):
            return self._error("Click", "The selected object has no interactive accessibility strategy.")
        self._set_status("Clicking %s…" % component_id)
        threading.Thread(
            target=self._click_selected_worker,
            args=(definition,),
            name="automation-author-click",
            daemon=True,
        ).start()

    def _click_selected_worker(self, definition) -> None:
        errors = []
        for strategy in definition.strategies:
            identification = strategy.options.get("identification")
            try:
                if strategy.type == "atspi":
                    result = AtspiDriver().activate(identification=identification)
                elif strategy.type == "java_accessibility":
                    result = JavaAccessibilityDriver().activate(identification=identification)
                elif strategy.type == "javafx":
                    result = JavaFxBridgeDriver().activate(identification=identification)
                else:
                    continue
            except Exception as exc:
                errors.append("%s: %s: %s" % (strategy.type, type(exc).__name__, exc))
                continue
            GLib.idle_add(self._finish_selected_click, definition.component_id, result, None)
            return
        GLib.idle_add(
            self._finish_selected_click,
            definition.component_id,
            None,
            RuntimeError("; ".join(errors) or "no compatible accessibility strategy"),
        )

    def _finish_selected_click(self, component_id, result=None, error=None):
        if error is not None:
            self._set_status("Click failed")
            self._error("Click failed", "%s: %s" % (type(error).__name__, error))
            return False
        self._set_status("Clicked %s" % component_id)
        self._set_text(
            self.object_detail,
            json.dumps({"component_id": component_id, "click": result}, indent=2, default=str),
        )
        return False

    def approve_visual_candidate(self) -> None:
        if self.repository_path is None: return self._error("Visual approval", "Open an editable repository first.")
        component_id = self._selected(self.object_tree)
        if not component_id: return self._info("Visual approval", "Select a component first.")
        key = self._ask_text("Approve visual candidate", "Variant key:")
        if not key: return
        try:
            definition = approve_visual_candidate(self.repository_path, component_id, key)
            self.repository = self._load_repository(); self.refresh_objects(); self._set_status("Approved visual revision %s" % definition.visual["revision"])
        except Exception as exc: self._error("Visual approval", "%s: %s" % (type(exc).__name__, exc))

    def reject_visual_candidate(self) -> None:
        if self.repository_path is None: return self._error("Visual rejection", "Open an editable repository first.")
        component_id = self._selected(self.object_tree)
        if not component_id: return self._info("Visual rejection", "Select a component first.")
        key = self._ask_text("Reject visual candidate", "Variant key:")
        if not key: return
        try: reject_visual_candidate(self.repository_path, component_id, key); self._set_status("Rejected visual candidate " + key)
        except Exception as exc: self._error("Visual rejection", "%s: %s" % (type(exc).__name__, exc))

    def refresh_steps(self) -> None:
        selected = self._selected(self.step_tree, 1)
        self.step_store.clear()
        component_id = self._selected(self.object_tree)
        if not component_id:
            self.step_count.set_text("Select an object")
            self._set_text(self.step_detail, "Select a captured object to see its supported actions.")
            return
        query = self.step_filter.get_text().strip().casefold()
        definitions = actions_for(self.repository.get(component_id))
        visible = 0
        for definition in definitions:
            searchable = "%s %s %s" % (definition.name, definition.category, definition.description)
            if query and query not in searchable.casefold():
                continue
            self.step_store.append((definition.name, definition.action_id, definition.category, definition.description))
            visible += 1
        self.step_count.set_text("%d of %d" % (visible, len(definitions)) if query else "%d actions" % len(definitions))
        self._select_value(self.step_tree, selected, 1)

    def show_step(self) -> None:
        action_id = self._selected(self.step_tree, 1)
        component_id = self._selected(self.object_tree)
        if not action_id or not component_id:
            return
        definition = action_by_id(self.repository.get(component_id), action_id)
        self.selected_action = action_id
        payload = {
            "action": definition.action_id,
            "name": definition.name,
            "category": definition.category,
            "description": definition.description,
            "object": component_id,
            "inputs": [vars(item) for item in definition.inputs],
        }
        self._set_text(self.step_detail, json.dumps(payload, indent=2, default=str))

    def add_selected_step(self) -> None:
        action_id = self._selected(self.step_tree, 1)
        component_id = self._selected(self.object_tree)
        if not component_id:
            return self._info("Actions", "Select a captured object first.")
        if not action_id:
            return self._info("Actions", "Select an action first.")
        definition = action_by_id(self.repository.get(component_id), action_id)
        values = self._configure_action(definition)
        if values is None:
            return
        call = definition.to_step_call(_next_node_id(self.plan.steps), component_id, values)
        self.plan = replace(self.plan, steps=self.plan.steps + (call,))
        self.refresh_plan(); self.refresh_state()
        self.notebook.set_current_page(2)
        self._select_value(self.plan_tree, call.node_id)
        self._set_status("Added %s on %s as a test step" % (definition.name, component_id))

    def _configure_action(self, definition):
        if not definition.inputs:
            return {}
        dialog = Gtk.Dialog(title="Configure %s" % definition.name, transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add to Test", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        entries = {}
        for item in definition.inputs:
            row = Gtk.Box(spacing=8)
            label = Gtk.Label(label=item.name + (" *" if item.required else "")); label.set_xalign(0); label.set_size_request(150, -1)
            entry = Gtk.Entry()
            if item.default is not None: entry.set_text(json.dumps(item.default))
            entry.set_placeholder_text(item.description or item.value_type)
            row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0); box.pack_start(row, False, False, 0)
            entries[item.name] = (item, entry)
        error = Gtk.Label(); error.set_xalign(0); box.pack_start(error, False, False, 0)
        dialog.show_all()
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                dialog.destroy(); return None
            values = {}
            missing = []
            for name, (item, entry) in entries.items():
                raw = entry.get_text().strip()
                if not raw:
                    if item.required: missing.append(name)
                    elif item.default is not None: values[name] = item.default
                    continue
                try: values[name] = json.loads(raw)
                except ValueError: values[name] = raw
            if not missing:
                dialog.destroy(); return values
            error.set_text("Required: " + ", ".join(missing))

    def add_script_step_dialog(self) -> None:
        if self.project is None:
            return self._info("Script step", "Create or open a project before adding project-registered script steps.")
        if not self.project.script_steps:
            return self._info("Script step", "This project has no script_steps manifests registered.")

        available = []
        for manifest in self.project.script_steps:
            try:
                available.append(ScriptStepDefinition.load(manifest).step_id)
            except Exception as exc:
                return self._error("Script step", "%s: %s" % (type(exc).__name__, exc))
        available = sorted(set(available))
        selected = self._ask_text(
            "Add Script Step",
            "Registered script step ID:\n" + "\n".join(available),
            available[0] if len(available) == 1 else "",
        )
        if not selected:
            return
        selected = selected.strip()
        if selected not in available:
            return self._error(
                "Script step",
                "Unknown project script step %r. Available: %s" % (selected, ", ".join(available)),
            )

        try:
            metadata = registered_script_step(selected)
            definition = self.registry.get(selected)
        except Exception as exc:
            return self._error("Script step", "%s: %s" % (type(exc).__name__, exc))

        dialog = Gtk.Dialog(title="Configure %s" % selected, transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add to Test", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        description = Gtk.Label(label=metadata.description or selected)
        description.set_xalign(0)
        box.pack_start(description, False, False, 0)

        input_entries = {}
        for item in definition.inputs:
            row = Gtk.Box(spacing=8)
            label = Gtk.Label(label="%s%s (%s)" % (item.name, " *" if item.required else "", item.annotation))
            label.set_xalign(0); label.set_size_request(220, -1)
            entry = Gtk.Entry()
            if not item.required:
                entry.set_text(json.dumps(item.default))
            entry.set_placeholder_text("JSON value" + (" — required" if item.required else ""))
            row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0); box.pack_start(row, False, False, 0)
            input_entries[item.name] = (item, entry)

        output_entries = {}
        if definition.outputs:
            box.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)
            caption = Gtk.Label(label="Output bindings"); caption.set_xalign(0); box.pack_start(caption, False, False, 0)
        for output in definition.outputs:
            row = Gtk.Box(spacing=8)
            label = Gtk.Label(label=output.name); label.set_xalign(0); label.set_size_request(220, -1)
            entry = Gtk.Entry(); entry.set_text(output.name); entry.set_placeholder_text("test variable name; blank to ignore")
            row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0); box.pack_start(row, False, False, 0)
            output_entries[output.name] = entry

        error = Gtk.Label(); error.set_xalign(0); box.pack_start(error, False, False, 0)
        dialog.show_all()
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                dialog.destroy(); return
            try:
                inputs = {}
                missing = []
                for name, (item, entry) in input_entries.items():
                    raw = entry.get_text().strip()
                    if not raw:
                        if item.required: missing.append(name)
                        continue
                    try: inputs[name] = json.loads(raw)
                    except json.JSONDecodeError: inputs[name] = raw
                if missing:
                    error.set_text("Required inputs: " + ", ".join(missing)); continue
                outputs = {name: entry.get_text().strip() for name, entry in output_entries.items() if entry.get_text().strip()}
                call = StepCall(node_id=_next_node_id(self.plan.steps), step_id=selected, inputs=inputs, outputs=outputs)
                candidate = replace(self.plan, steps=self.plan.steps + (call,))
                issues = validate_plan(candidate, self.registry)
                if issues:
                    error.set_text("; ".join(issues)); continue
            except Exception as exc:
                error.set_text("%s: %s" % (type(exc).__name__, exc)); continue
            dialog.destroy()
            self.plan = candidate
            self.refresh_plan(); self.refresh_state()
            self.notebook.set_current_page(2)
            self._select_value(self.plan_tree, call.node_id)
            self._set_status("Added script step %s to test" % selected)
            return

    def duplicate_plan_step(self) -> None:
        node_id = self._selected(self.plan_tree)
        if not node_id: return
        steps = list(self.plan.steps)
        index = next(index for index, item in enumerate(steps) if item.node_id == node_id)
        duplicate = replace(steps[index], node_id=_next_node_id(self.plan.steps))
        steps.insert(index + 1, duplicate)
        self.plan = replace(self.plan, steps=tuple(steps)); self.refresh_state(); self._select_value(self.plan_tree, duplicate.node_id)

    def move_plan_step(self, offset) -> None:
        node_id = self._selected(self.plan_tree)
        if not node_id: return
        steps = list(self.plan.steps)
        index = next(index for index, item in enumerate(steps) if item.node_id == node_id)
        target = index + offset
        if target < 0 or target >= len(steps): return
        steps[index], steps[target] = steps[target], steps[index]
        self.plan = replace(self.plan, steps=tuple(steps)); self.refresh_state(); self._select_value(self.plan_tree, node_id)

    def edit_plan_step(self) -> None:
        node_id = self._selected(self.plan_tree)
        if not node_id: return
        call = next(item for item in self.plan.steps if item.node_id == node_id)
        raw = self._ask_text("Edit step", "JSON object with inputs and outputs:", json.dumps({"inputs": _encode_gui(call.inputs), "outputs": dict(call.outputs)}, indent=2), multiline=True)
        if raw is None: return
        try:
            payload = json.loads(raw); inputs = _decode_gui(payload.get("inputs", {})); outputs = payload.get("outputs", {})
            updated = replace(call, inputs=inputs, outputs={str(k): str(v) for k, v in outputs.items()})
            self.plan = replace(self.plan, steps=tuple(updated if item.node_id == node_id else item for item in self.plan.steps)); self.refresh_plan(); self.refresh_state()
        except Exception as exc: self._error("Invalid step data", str(exc))

    def remove_plan_step(self) -> None:
        node_id = self._selected(self.plan_tree)
        if node_id: self.plan = replace(self.plan, steps=tuple(item for item in self.plan.steps if item.node_id != node_id)); self.refresh_plan(); self.refresh_state()

    def start_recording(self) -> None:
        if self.recording_session is not None and self.recording_session.active:
            return
        if self.recording_session_factory is not None:
            self.recording_session = self.recording_session_factory(self.repository)
        else:
            urls = os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_URLS", os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_URL", "")).split(",")
            tokens = os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_TOKENS", os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_TOKEN", "")).split(",")
            adapters = []
            atspi = AtspiRecordingAdapter()
            if atspi.available:
                adapters.append(atspi)
            adapters.extend(JavaFxRecordingAdapter(HttpJavaFxBridgeTransport(url.strip(), token.strip())) for url, token in zip(urls, tokens) if url.strip() and token.strip())
            if not adapters:
                return self._error("Recording", "No AT-SPI desktop session or configured JavaFX recording agent is available.")
            self.recording_session = RecordingSession(adapters, repository=self.repository)
        try:
            self.recording_session.start()
        except Exception as exc:
            self.recording_session = None
            self._error("Recording", "%s: %s" % (type(exc).__name__, exc))
            return
        self.start_recording_button.set_sensitive(False)
        self.stop_recording_button.set_sensitive(True)
        self._show_recording_stop_window()
        self._set_status("Recording… interact with the target application, then stop recording")

    def _show_recording_stop_window(self) -> None:
        self.window.hide()
        stop = Gtk.Window(type=Gtk.WindowType.TOPLEVEL)
        stop.set_title("Recording")
        stop.set_keep_above(True)
        stop.set_decorated(False)
        stop.set_border_width(10)
        button = Gtk.Button(label="Stop Recording")
        button.set_size_request(180, 52)
        button.connect("clicked", lambda *_args: self.stop_recording())
        stop.add(button)
        stop.connect("delete-event", lambda *_args: (self.stop_recording(), True)[1])
        stop.set_position(Gtk.WindowPosition.CENTER)
        stop.show_all()
        self.recording_stop_window = stop
        self.recording_stop_button = button

    def _restore_after_recording(self) -> None:
        if self.recording_stop_window is not None:
            self.recording_stop_window.destroy()
            self.recording_stop_window = None
        self.recording_stop_button = None
        self.window.show_all()
        self.window.present()

    def stop_recording(self) -> None:
        if self.recording_session is None or self._recording_stop_active:
            return
        self._recording_stop_active = True
        self.stop_recording_button.set_sensitive(False)
        if self.recording_stop_button is not None:
            self.recording_stop_button.set_sensitive(False)
            self.recording_stop_button.set_label("Stopping…")
        self._set_status("Stopping recording…")
        # Restore the main window before waiting on accessibility transports.
        # Keeping GTK's main loop live also lets AT-SPI finish any callback
        # already resolving the floating stop control.
        self._restore_after_recording()
        session = self.recording_session

        def worker():
            try:
                interactions = list(session.stop())
            except Exception as exc:
                GLib.idle_add(self._finish_recording_stop, None, exc)
            else:
                GLib.idle_add(self._finish_recording_stop, interactions, None)

        threading.Thread(target=worker, name="automation-recording-stop", daemon=True).start()

    def _finish_recording_stop(self, interactions=None, error=None):
        self._recording_stop_active = False
        self.recording_session = None
        self.start_recording_button.set_sensitive(True)
        self.stop_recording_button.set_sensitive(False)
        self._restore_after_recording()
        if error is not None:
            self._set_status("Recording failed to stop cleanly")
            self._error("Recording", "%s: %s" % (type(error).__name__, error))
            return False
        self.recorded_interactions = list(interactions or ())
        self.refresh_recorded_interactions()
        new_count = sum(item.repository_match.status == "new_candidate" for item in self.recorded_interactions)
        self._set_status("Recording stopped: %d interactions, %d new component candidates" % (len(self.recorded_interactions), new_count))
        if new_count:
            self._save_recorded_objects_dialog()
        return False

    def refresh_recorded_interactions(self) -> None:
        self.recording_store.clear()
        for index, interaction in enumerate(self.recorded_interactions):
            target_name = _recorded_target_label(interaction)
            match = interaction.repository_match
            self.recording_store.append((str(index), interaction.action.value, target_name, json.dumps(dict(interaction.parameters), separators=(",", ":")), match.component_id or match.status.replace("_", " "), "%.0f%%" % (interaction.confidence * 100)))

    def _save_recorded_objects_dialog(self) -> None:
        if self._recorded_object_save_active:
            return
        dialog = Gtk.Dialog(title="Save recorded objects", transient_for=self.window, modal=True)
        dialog.add_buttons(
            "Later", Gtk.ResponseType.CANCEL,
            "Existing Repository", 1,
            "Current Test Plan", 2,
            "New Repository", 3,
        )
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        label = Gtk.Label(label="Where should newly discovered objects be saved?")
        label.set_xalign(0); box.pack_start(label, False, False, 0)
        dialog.show_all(); response = dialog.run(); dialog.destroy()
        if response == Gtk.ResponseType.CANCEL:
            return
        destination = self.repository_path
        mode = "current"
        if response == 1:
            mode = "existing"
        elif response == 3:
            mode = "new"
        elif response == 2:
            destination = None
            mode = "plan"
        if mode == "existing":
            filename = self._choose_file(yaml=True, artifact_suffix=REPOSITORY_SUFFIX, title="Select Existing Object Repository")
            if not filename: return
            destination = Path(filename)
        elif mode == "new":
            filename = self._choose_file(save=True, yaml=True, artifact_suffix=REPOSITORY_SUFFIX, title="Save New Object Repository")
            if not filename: return
            destination = Path(filename)
        self._recorded_object_save_active = True
        self._set_status("Saving recorded objects…")
        interactions = tuple(self.recorded_interactions)

        def worker():
            try:
                repository = (
                    ComponentRepository.load([destination]) if mode == "existing"
                    else ComponentRepository({}) if mode == "new"
                    else self.repository
                )
                result = self._build_recorded_objects(repository, interactions)
                if destination is not None:
                    result[0].save(Path(destination))
            except Exception as exc:
                GLib.idle_add(self._finish_recorded_object_save, None, destination, mode, exc)
            else:
                GLib.idle_add(self._finish_recorded_object_save, result, destination, mode, None)

        threading.Thread(target=worker, name="automation-recorded-object-save", daemon=True).start()

    def _build_recorded_objects(self, repository, interactions):
        updated = list(interactions)
        saved_ids = []
        for index, interaction in enumerate(updated):
            if interaction.repository_match.status != "new_candidate" or interaction.target is None:
                continue
            component_id = _recorded_component_id(interaction.target, repository)
            definition = self.capture.definition_from_capture(component_id, interaction.target)
            repository = repository.with_component(definition)
            updated[index] = replace(interaction, repository_match=RepositoryMatch("known_unique", (component_id,)))
            saved_ids.append(component_id)
        return repository, updated, saved_ids

    def _finish_recorded_object_save(self, result, destination, mode, error):
        self._recorded_object_save_active = False
        if error is not None:
            self._set_status("Recorded objects were not saved")
            self._error("Save recorded objects", "%s: %s" % (type(error).__name__, error))
            return False
        repository, updated, saved_ids = result
        self.repository = repository
        self.recorded_interactions = updated
        if mode == "plan":
            document = repository.to_document()["components"]
            inline = dict(self.plan.objects)
            inline.update({component_id: document[component_id] for component_id in saved_ids})
            self.plan = replace(self.plan, objects=inline)
        elif destination is not None:
            self.repository_path = Path(destination)
        self.refresh_objects(); self.refresh_recorded_interactions()
        location = "current test plan" if destination is None else str(destination)
        self._set_status("Saved %d recorded object(s) to %s" % (len(saved_ids), location))
        return False

    def keep_recorded_interaction(self) -> None:
        if self._selected(self.recording_tree) is not None:
            self._set_status("Recorded interaction kept for review")

    def delete_recorded_interaction(self) -> None:
        index = self._selected(self.recording_tree)
        if index is None:
            return
        del self.recorded_interactions[int(index)]
        self.refresh_recorded_interactions()

    def add_recorded_interaction_to_test(self) -> None:
        index = self._selected(self.recording_tree)
        if index is None:
            return self._info("Recording", "Select a recorded interaction first.")
        try:
            call = interactions_to_steps((self.recorded_interactions[int(index)],), start_index=len(self.plan.steps) + 1)[0]
        except Exception as exc:
            return self._info("Recording", "This interaction cannot be added yet: %s" % exc)
        self.plan = replace(self.plan, steps=(*self.plan.steps, call))
        self.refresh_plan(); self.refresh_state()
        self._set_status("Added reviewed recorded interaction as a test step")

    def refresh_plan(self) -> None:
        self.plan = replace(self.plan, name=self.plan_name.get_text().strip() or "new-test-plan")
        self.plan_store.clear()
        for call in self.plan.steps: self.plan_store.append((call.node_id, call.step_id, json.dumps(_encode_gui(call.inputs), separators=(",", ":")), json.dumps(dict(call.outputs), separators=(",", ":")), ",".join(call.depends_on)))

    def refresh_variables(self) -> None: self._set_text(self.variables_text, json.dumps(dict(self.plan.variables), indent=2, default=str))

    def apply_variables(self) -> None:
        try:
            values = json.loads(self._get_text(self.variables_text).strip() or "{}")
            if not isinstance(values, dict): raise ValueError("variables must be a JSON object")
            self.plan = replace(self.plan, variables=values); self.refresh_state()
        except Exception as exc: self._error("Invalid variables", str(exc))

    def refresh_state(self) -> None:
        self.refresh_plan(); self.state_caption.set_text("Pre-execution managed queue projection"); self.state_store.clear()
        for node_id, item in derive_execution_state(self.plan).steps.items(): self.state_store.append((node_id, item.step_id, item.status.value, ", ".join(item.unresolved_variables)))

    def validate_plan_dialog(self) -> None:
        self.refresh_plan(); issues = validate_plan(self.plan, self.registry); issues.extend(validate_plan_components(self.plan, self.repository))
        self._error("Plan validation", "\n".join(issues)) if issues else self._info("Plan validation", "Plan is structurally valid against the current registered-step catalog.")

    def open_plan_dialog(self) -> None:
        path = self._choose_file(yaml=True, artifact_suffix=PLAN_SUFFIX, title="Open Test Plan")
        if not path: return
        try:
            self.plan = load_plan(Path(path))
            self.plan_path = Path(path)
            inline = repository_from_plan(self.plan)
            if inline.components:
                self.repository = inline
                self.repository_path = None
                self.refresh_objects()
            self.plan_name.set_text(self.plan.name); self.refresh_plan(); self.refresh_variables(); self.refresh_state(); self._set_status("Opened plan: " + path)
        except Exception as exc: self._error("Plan error", "%s: %s" % (type(exc).__name__, exc))

    def save_plan_dialog(self) -> None:
        self.refresh_plan(); issues = validate_plan(self.plan, self.registry)
        if issues and not self._confirm("Plan has validation issues", "\n".join(issues) + "\n\nSave anyway?"): return
        path = str(self.plan_path) if self.plan_path is not None else self._choose_file(save=True, yaml=True, artifact_suffix=PLAN_SUFFIX, title="Save Test Plan")
        if path:
            self.plan = embed_plan_repository(self.plan, self.repository)
            self.plan_path = Path(path)
            save_plan(self.plan, self.plan_path); self._set_status("Saved plan: " + str(self.plan_path))

    def save_plan_as_dialog(self) -> None:
        previous = self.plan_path
        self.plan_path = None
        self.save_plan_dialog()
        if self.plan_path is None:
            self.plan_path = previous

    def new_project_dialog(self) -> None:
        path = self._choose_file(save=True, yaml=True, artifact_suffix=PROJECT_SUFFIX, title="Create Test Project")
        if not path:
            return
        name = self._ask_text("New Test Project", "Project name:", artifact_stem(Path(path), PROJECT_SUFFIX))
        if not name:
            return
        try:
            self.project_path = Path(path)
            self.project = create_authoring_project(self.project_path, name)
            self.repository_path = self.project.repository
            self.repository = self._load_repository()
        except Exception as exc:
            return self._error("New project", "%s: %s" % (type(exc).__name__, exc))
        self.refresh_all(); self._set_status("Created project — capture an object or add a registered script step")

    def open_project_dialog(self) -> None:
        path = self._choose_file(yaml=True, artifact_suffix=PROJECT_SUFFIX, title="Open Test Project")
        if not path:
            return
        try:
            project = AuthoringProject.load(Path(path))
            self.project_path = Path(path); self.project = project
            self.repository_path = project.repository; self.repository = self._load_repository()
        except Exception as exc:
            return self._error("Open project", "%s: %s" % (type(exc).__name__, exc))
        self.refresh_all(); self._set_status("Opened project: " + project.name)

    def save_reusable_step(self) -> None:
        self.refresh_plan()
        if not self.plan.steps:
            return self._info("Reusable step", "Add one or more actions to the test first.")
        if self.project is None:
            return self._info("Reusable step", "Open an authoring project with --project before saving reusable steps.")
        step_id = self._ask_text("Save as Reusable Step", "Reusable step ID (for example authentication.login):")
        if not step_id:
            return
        name = self._ask_text("Save as Reusable Step", "Display name:", self.plan.name)
        if not name:
            return
        try:
            definition = ReusableStepDefinition(step_id, name, "", self.plan, {}, {})
            path = definition.save(self.project.root / "reusable_steps")
        except Exception as exc:
            return self._error("Reusable step", "%s: %s" % (type(exc).__name__, exc))
        self._set_status("Saved reusable step: " + str(path))
        self._info("Reusable step", "Saved %s. Reusable Steps now contains this user-authored composition." % name)

    def run_reference_plan(self) -> None:
        if self._run_active: return
        self.refresh_plan()
        issues = validate_plan(self.plan, self.registry); issues.extend(validate_plan_components(self.plan, self.repository))
        if issues: return self._error("Plan validation", "\n".join(issues))
        self._run_active = True; self.run_reference_button.set_sensitive(False)
        self._set_status("Running test against the current desktop environment…")
        plan = self.plan
        runs_dir = self.project.runs_dir if self.project else (Path.cwd() / "runs").resolve()

        def worker():
            try:
                result = execute_plan(
                    plan,
                    LiveDesktopBackend(),
                    runs_dir=runs_dir,
                    component_repository=self.repository,
                )
            except Exception as exc:
                GLib.idle_add(self._present_run_error, exc); return
            GLib.idle_add(self._present_reference_result, result)

        threading.Thread(target=worker, name="automation-author-run", daemon=True).start()

    def _present_run_error(self, error):
        self._run_active = False; self.run_reference_button.set_sensitive(True); self._set_status("Test run failed to start")
        self._error("Test run", "%s: %s" % (type(error).__name__, error)); return False

    def _present_reference_result(self, result):
        self._run_active = False; self.run_reference_button.set_sensitive(True); self.last_run_dir = result.artifact_dir
        status = "PASS" if result.exit_code == 0 else "FAIL"; self._set_status("%s: test run %s" % (status, result.run_id))
        detail = "Passed: %s\nFailed: %s\nExit code: %s" % (result.passed, result.failed, result.exit_code)
        if result.validation_errors: detail += "\n\n" + "\n".join(result.validation_errors)
        self._info("Test run", detail) if result.exit_code == 0 else self._error("Test run", detail)
        return False

    def open_repository(self) -> None:
        path = self._choose_file(yaml=True, artifact_suffix=REPOSITORY_SUFFIX, title="Open Object Repository")
        if not path: return
        self.repository_path = Path(path)
        try: self.repository = self._load_repository(); self.refresh_objects()
        except Exception as exc: self._error("Repository error", str(exc))

    def _message(self, kind, title, text, buttons=Gtk.ButtonsType.OK):
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=kind, buttons=buttons, text=title)
        dialog.format_secondary_text(str(text)); response = dialog.run(); dialog.destroy(); return response

    def _info(self, title, text): return self._message(Gtk.MessageType.INFO, title, text)
    def _error(self, title, text): return self._message(Gtk.MessageType.ERROR, title, text)
    def _confirm(self, title, text): return self._message(Gtk.MessageType.QUESTION, title, text, Gtk.ButtonsType.YES_NO) == Gtk.ResponseType.YES

    def _ask_text(self, title, prompt, initial="", multiline=False):
        dialog = Gtk.Dialog(title=title, transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(6); box.set_border_width(8); box.pack_start(Gtk.Label(label=prompt), False, False, 0)
        if multiline:
            widget = Gtk.TextView(); widget.set_monospace(True); widget.get_buffer().set_text(initial); scroll = self._scrolled(widget); scroll.set_size_request(620, 320); box.pack_start(scroll, True, True, 0)
        else:
            widget = Gtk.Entry(); widget.set_text(initial); box.pack_start(widget, False, False, 0)
        dialog.show_all(); response = dialog.run()
        value = self._get_text(widget) if multiline else widget.get_text(); dialog.destroy()
        return value if response == Gtk.ResponseType.OK else None

    def _choose_file(self, save=False, yaml=False, artifact_suffix=None, title="Select file"):
        action = Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN
        dialog = Gtk.FileChooserDialog(title=title, transient_for=self.window, action=action)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save" if save else "Open", Gtk.ResponseType.OK)
        if save: dialog.set_do_overwrite_confirmation(True)
        if yaml:
            filt = Gtk.FileFilter(); filt.set_name("Automation Harness YAML")
            if artifact_suffix: filt.add_pattern("*" + artifact_suffix)
            filt.add_pattern("*.yaml"); filt.add_pattern("*.yml"); dialog.add_filter(filt)
        if save and artifact_suffix:
            dialog.set_current_name("untitled" + artifact_suffix)
        response = dialog.run(); filename = dialog.get_filename() if response == Gtk.ResponseType.OK else None; dialog.destroy()
        if filename and save and artifact_suffix:
            filename = str(with_artifact_suffix(Path(filename), artifact_suffix))
        return filename


def _encode_gui(value: Any) -> Any:
    if isinstance(value, PlanVariableRef): return {"$var": value.path}
    if isinstance(value, dict): return {key: _encode_gui(item) for key, item in value.items()}
    if isinstance(value, list): return [_encode_gui(item) for item in value]
    return value


def _decode_gui(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"$var"} and isinstance(value["$var"], str): return PlanVariableRef(value["$var"])
        return {key: _decode_gui(item) for key, item in value.items()}
    if isinstance(value, list): return [_decode_gui(item) for item in value]
    return value


def _next_node_id(steps):
    existing = {item.node_id for item in steps}
    index = 1
    while "step-%03d" % index in existing:
        index += 1
    return "step-%03d" % index


def _recorded_target_label(interaction):
    match = interaction.repository_match
    if match.component_id:
        return match.component_id
    target = interaction.target
    if target is None:
        return "Unresolved target"
    physical = target.backend_properties.get("physical_target", {})
    if isinstance(physical, dict):
        for key in ("accessible_id", "name", "text"):
            value = physical.get(key)
            if value and str(value).casefold() != "main":
                return str(value)
    for value in (target.accessible_id, target.name):
        if value and str(value).casefold() != "main":
            return str(value)
    return "%s (%s)" % (target.role or "object", target.native_class or target.framework or "unknown")


def _recorded_component_id(target, repository):
    parts = [target.application, target.name, target.accessible_id, target.role]
    normalized = []
    for part in parts:
        if not part or str(part).casefold() == "main":
            continue
        value = "".join(character.casefold() if character.isalnum() else "_" for character in str(part))
        value = "_".join(item for item in value.split("_") if item)
        if value and value not in normalized:
            normalized.append(value)
    base = ".".join(normalized[:2]) or "recorded.object"
    candidate = base
    serial = 2
    while repository.contains(candidate):
        candidate = "%s_%d" % (base, serial); serial += 1
    return candidate


def _highlight_rectangles(bounds, thickness=4):
    x, y, width, height = bounds
    if width <= 0 or height <= 0: raise ValueError("highlight bounds require positive width and height")
    edge = max(1, min(thickness, width, height))
    return ((x, y, width, edge), (x, y + height - edge, width, edge), (x, y, edge, height), (x + width - edge, y, edge, height))


def main(argv=None): return _launch(argv, mode="author", prog="automation-author", description="Local automation authoring GUI")
def capture_main(argv=None): return _launch(argv, mode="capture", prog="automation-capture", description="Object Capture / Object Spy GUI")
def repository_main(argv=None): return _launch(argv, mode="repository", prog="automation-repository", description="Object Repository editor GUI")


def _launch(argv, *, mode, prog, description):
    import argparse
    parser = argparse.ArgumentParser(prog=prog, description=description)
    parser.add_argument("--repository", type=Path)
    parser.add_argument("--project", type=Path, help="authoring project manifest")
    parser.add_argument("--smoke-test", action="store_true", help="construct and render the GTK GUI once, then exit")
    args = parser.parse_args(argv)
    app = AuthoringApp(args.repository, mode=mode, project_path=args.project)
    if args.smoke_test:
        while Gtk.events_pending(): Gtk.main_iteration_do(False)
        app.window.destroy(); return 0
    Gtk.main(); return 0


if __name__ == "__main__": raise SystemExit(main())
