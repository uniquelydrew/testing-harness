from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import cairo  # noqa: F401
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.backends.reference import ReferenceBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_capture import ObjectCaptureService
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import derive_execution_state, load_plan, save_plan, validate_plan, validate_plan_components
from automation_harness.core.visual_baselines import approve_visual_candidate, reject_visual_candidate
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan
from automation_harness.runner.plan_execution import execute_plan


class AuthoringApp:
    """GTK3 authoring/Object Capture client for the RHEL deployment target."""

    def __init__(self, repository_path: Path | None = None, *, mode: str = "author") -> None:
        self.mode = mode
        self.window = Gtk.Window(title={
            "capture": "Automation Harness Object Capture",
            "repository": "Automation Harness Object Repository",
        }.get(mode, "Automation Harness Author"))
        self.window.set_default_size(1180, 760)
        self.window.connect("destroy", lambda *_args: Gtk.main_quit())
        self.registry = default_step_registry()
        self.capture = ObjectCaptureService()
        self.repository_path = repository_path
        self.repository = self._load_repository()
        self.plan = TestPlan(name="new-test-plan")
        self.selected_step = None
        self._run_active = False
        self._click_capture_active = False
        self._click_picker = None
        self._click_picker_timeout = None
        self._last_capture = None
        self._highlight_windows = []
        self._highlight_timeout = None
        self.last_run_dir = None
        self._build()
        self.window.connect("key-press-event", self._on_key_press)
        self.refresh_all()
        self.window.show_all()

    def _load_repository(self) -> ComponentRepository:
        package_repo = Path(__file__).resolve().parents[1] / "resources" / "components.yaml"
        paths = [package_repo]
        if self.repository_path is not None:
            paths.append(self.repository_path)
        return ComponentRepository.load(paths)

    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(6)
        self.window.add(outer)

        toolbar = Gtk.Box(spacing=6)
        outer.pack_start(toolbar, False, False, 0)
        self._button(toolbar, "Open Repository", self.open_repository)
        if self.mode == "author":
            self._button(toolbar, "Open Plan", self.open_plan_dialog)
            self._button(toolbar, "Save Plan", self.save_plan_dialog)
            self._button(toolbar, "Validate Plan", self.validate_plan_dialog)
            self.run_reference_button = self._button(toolbar, "Run Reference", self.run_reference_plan)
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
        notebook.append_page(self.steps_tab, Gtk.Label(label="Step Library"))
        notebook.append_page(self.plan_tab, Gtk.Label(label="Test Composer"))
        notebook.append_page(self.vars_tab, Gtk.Label(label="Variables"))
        notebook.append_page(self.state_tab, Gtk.Label(label="Execution State"))
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
        self.object_tree.get_selection().connect("changed", lambda *_args: self.show_object())
        left.pack_start(self._scrolled(self.object_tree), True, True, 0)
        buttons = Gtk.Box(spacing=5)
        left.pack_start(buttons, False, False, 0)
        self._button(buttons, "Inspect", self.show_object)
        self._button(buttons, "Highlight", self.highlight_selected_object)
        if self.mode != "capture":
            self._button(buttons, "Edit Selected", self.edit_selected_object)
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
        self.step_filter.set_placeholder_text("Step ID, domain, or signature")
        self.step_filter.connect("search-changed", lambda *_args: self.refresh_steps())
        filter_row.pack_start(self.step_filter, True, True, 0)
        self.step_count = Gtk.Label(label="0 steps")
        filter_row.pack_end(self.step_count, False, False, 0)
        self.step_tree, self.step_store = self._tree((("Step", 300), ("Domain", 120), ("Signature", 420)))
        self.step_tree.get_selection().connect("changed", lambda *_args: self.show_step())
        self.step_tree.connect("row-activated", lambda *_args: self.add_selected_step())
        left.pack_start(self._scrolled(self.step_tree), True, True, 0)
        self._button(left, "Add Selected Step to Plan", self.add_selected_step)
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
        self.plan_tree, self.plan_store = self._tree((("Node", 100), ("Registered Step", 260), ("Inputs", 300), ("Outputs", 220), ("Depends", 140)))
        self.plan_tree.connect("row-activated", lambda *_args: self.edit_plan_step())
        self.plan_tree.connect("key-press-event", self._on_plan_tree_key_press)
        self.plan_tab.pack_start(self._scrolled(self.plan_tree), True, True, 0)

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
        self._set_status("Move pointer over target object…")
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
        self._set_status("Click the target object within 30 seconds…")
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
            path = self._choose_file(save=True, yaml=True)
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
        selected = self._selected(self.step_tree)
        self.step_store.clear()
        query = self.step_filter.get_text().strip().casefold()
        definitions = self.registry.definitions()
        visible = 0
        for definition in definitions:
            searchable = "%s %s %s" % (definition.name, definition.domain, definition.invocation_signature)
            if query and query not in searchable.casefold():
                continue
            self.step_store.append((definition.name, definition.domain, str(definition.invocation_signature)))
            visible += 1
        self.step_count.set_text("%d of %d" % (visible, len(definitions)) if query else "%d steps" % len(definitions))
        self._select_value(self.step_tree, selected)

    def show_step(self) -> None:
        name = self._selected(self.step_tree)
        if not name: return
        self.selected_step = name; self._set_text(self.step_detail, json.dumps(self.registry.get(name).to_dict(), indent=2, default=str))

    def add_selected_step(self) -> None:
        name = self._selected(self.step_tree)
        if not name: return
        definition = self.registry.get(name); node_id = _next_node_id(self.plan.steps)
        inputs = {item.name: item.default for item in definition.inputs if not item.required}
        self.plan = replace(self.plan, steps=self.plan.steps + (StepCall(node_id=node_id, step_id=definition.name, inputs=inputs),)); self.refresh_plan(); self.refresh_state()

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
        path = self._choose_file(yaml=True)
        if not path: return
        try:
            self.plan = load_plan(Path(path)); self.plan_name.set_text(self.plan.name); self.refresh_plan(); self.refresh_variables(); self.refresh_state(); self._set_status("Opened plan: " + path)
        except Exception as exc: self._error("Plan error", "%s: %s" % (type(exc).__name__, exc))

    def save_plan_dialog(self) -> None:
        self.refresh_plan(); issues = validate_plan(self.plan, self.registry)
        if issues and not self._confirm("Plan has validation issues", "\n".join(issues) + "\n\nSave anyway?"): return
        path = self._choose_file(save=True, yaml=True)
        if path: save_plan(self.plan, Path(path)); self._set_status("Saved plan: " + path)

    def run_reference_plan(self) -> None:
        if self._run_active: return
        self.refresh_plan(); issues = validate_plan(self.plan, self.registry); issues.extend(validate_plan_components(self.plan, self.repository))
        if issues: return self._error("Plan validation", "\n".join(issues))
        self._run_active = True; self.run_reference_button.set_sensitive(False); self._set_status("Running against reference backend…")
        plan = self.plan; runs_dir = (Path.cwd() / "runs").resolve()
        def worker():
            result = execute_plan(plan, ReferenceBackend(gui=True, display_mode="auto"), runs_dir=runs_dir, component_repository=self.repository)
            GLib.idle_add(self._present_reference_result, result)
        threading.Thread(target=worker, daemon=True).start()

    def _present_reference_result(self, result):
        self._run_active = False; self.run_reference_button.set_sensitive(True); self.last_run_dir = result.artifact_dir
        status = "PASS" if result.exit_code == 0 else "FAIL"; self._set_status("%s: reference run %s" % (status, result.run_id))
        detail = "Passed: %s\nFailed: %s\nExit code: %s" % (result.passed, result.failed, result.exit_code)
        if result.validation_errors: detail += "\n\n" + "\n".join(result.validation_errors)
        self._info("Reference run", detail) if result.exit_code == 0 else self._error("Reference run", detail)
        return False

    def open_repository(self) -> None:
        path = self._choose_file(yaml=True)
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

    def _choose_file(self, save=False, yaml=False):
        action = Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN
        dialog = Gtk.FileChooserDialog(title="Select file", transient_for=self.window, action=action)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save" if save else "Open", Gtk.ResponseType.OK)
        if save: dialog.set_do_overwrite_confirmation(True)
        if yaml:
            filt = Gtk.FileFilter(); filt.set_name("YAML"); filt.add_pattern("*.yaml"); filt.add_pattern("*.yml"); dialog.add_filter(filt)
        response = dialog.run(); filename = dialog.get_filename() if response == Gtk.ResponseType.OK else None; dialog.destroy(); return filename


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
    parser.add_argument("--smoke-test", action="store_true", help="construct and render the GTK GUI once, then exit")
    args = parser.parse_args(argv)
    app = AuthoringApp(args.repository, mode=mode)
    if args.smoke_test:
        while Gtk.events_pending(): Gtk.main_iteration_do(False)
        app.window.destroy(); return 0
    Gtk.main(); return 0


if __name__ == "__main__": raise SystemExit(main())

