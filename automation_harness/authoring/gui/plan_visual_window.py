"""Test Plan workflow additions for visual matching and unobstructed execution."""
from __future__ import annotations

import json
import threading
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.steps import visual_assert_steps as _visual_assert_steps  # noqa: F401
from automation_harness.authoring.action_catalog import actions_for
from automation_harness.authoring.gui.plan_recording_window import RecordingTestPlanWindow
from automation_harness.authoring.gui.plan_window import _next_node_id
from automation_harness.authoring.preferences_runtime import AuthoringPreferences
from automation_harness.authoring.visual_match_assets import import_visual_match
from automation_harness.runner.plan_execution import execute_plan
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.test_plan import validate_plan, validate_plan_components
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.plan import StepCall


class VisualTestPlanWindow(RecordingTestPlanWindow):
    """Recording-capable Test Plan editor with explicit repository visual assertions."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.project is not None and self.project.object_repositories:
            self.repository = self.repository.overlay(ComponentRepository.load(self.project.object_repositories))
            self.refresh_all()

    def add_object_action(self):
        if not self.repository.components:
            return self.info("Object Action", "No objects are available. Open or capture objects in an Object Repository first.")
        dialog = Gtk.Dialog(title="Add Object Action", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        object_combo = Gtk.ComboBoxText()
        for component_id in sorted(self.repository.components): object_combo.append(component_id, component_id)
        object_combo.set_active(0)
        action_combo = Gtk.ComboBoxText(); inputs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        input_entries = {}; visual_combo = Gtk.ComboBoxText(); add_match_button = Gtk.Button(label="Add New Match Image…")
        match_percentage = Gtk.SpinButton.new_with_range(0.0, 100.0, 0.1)
        match_percentage.set_digits(1)

        def selected_definition():
            component_id = object_combo.get_active_id()
            return self.repository.get(component_id) if component_id else None

        def current_variant():
            definition = selected_definition(); key = visual_combo.get_active_id()
            if definition is None or not key: return None
            return (definition.visual or {}).get("variants", {}).get(key)

        def reset_match_percentage(*_args):
            variant = current_variant()
            if variant is None:
                match_percentage.set_value(99.0)
                return
            match_percentage.set_value(max(0.0, min(100.0, (1.0 - float(variant.get("max_difference_ratio", 0.01))) * 100.0)))

        def rebuild_visuals():
            visual_combo.remove_all(); definition = selected_definition()
            variants = (definition.visual or {}).get("variants", {}) if definition else {}
            for key, item in sorted(variants.items()): visual_combo.append(key, "%s — %s" % (key, item.get("image", "")))
            if variants: visual_combo.set_active(0)
            reset_match_percentage()

        def add_match(*_args):
            definition = selected_definition()
            if definition is None: return
            image = self.choose_file(title="Select Match Image")
            if image is None: return
            match_id = self.ask_text("Add Match Image", "Match image ID:", image.stem)
            if not match_id: return
            repository_path = definition.repository_path
            if repository_path is None and self.project is not None:
                for candidate in self.project.object_repositories:
                    candidate_repository = ComponentRepository.load([candidate])
                    if candidate_repository.contains(definition.component_id):
                        repository_path = candidate
                        break
            if repository_path is None:
                return self.error("Assert Match", "The selected object is not backed by a writable Object Repository. Add its repository to the Project first.")
            try:
                updated = import_visual_match(repository_path, definition.component_id, image, match_id)
                self.repository = self.repository.with_component(updated)
            except Exception as exc:
                return self.error("Assert Match", "%s: %s" % (type(exc).__name__, exc))
            rebuild_visuals(); visual_combo.set_active_id(match_id); reset_match_percentage(); self.mark_dirty()

        def rebuild_actions(*_args):
            action_combo.remove_all(); definition = selected_definition()
            if definition is None: return
            for action in actions_for(definition): action_combo.append(action.action_id, "%s — %s" % (action.name, action.category))
            action_combo.append("assert_match", "Assert Match — Assertion")
            action_combo.set_active(0); rebuild_visuals()

        def rebuild_inputs(*_args):
            for child in inputs_box.get_children(): child.destroy()
            input_entries.clear(); definition = selected_definition(); action_id = action_combo.get_active_id()
            if definition is None or not action_id: return
            if action_id == "assert_match":
                label = Gtk.Label(label="Match Image"); label.set_xalign(0)
                percentage_label = Gtk.Label(label="Minimum Match (%)"); percentage_label.set_xalign(0)
                inputs_box.pack_start(label, False, False, 0)
                inputs_box.pack_start(visual_combo, False, False, 0)
                inputs_box.pack_start(add_match_button, False, False, 0)
                inputs_box.pack_start(percentage_label, False, False, 0)
                inputs_box.pack_start(match_percentage, False, False, 0)
                inputs_box.show_all(); return
            action = next(item for item in actions_for(definition) if item.action_id == action_id)
            for item in action.inputs:
                row = Gtk.Box(spacing=6); label = Gtk.Label(label=item.name + (" *" if item.required else "")); label.set_size_request(170, -1); label.set_xalign(0)
                entry = Gtk.Entry(); entry.set_placeholder_text(item.description or item.value_type)
                if item.default is not None: entry.set_text(json.dumps(item.default, default=str))
                row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0); inputs_box.pack_start(row, False, False, 0); input_entries[item.name] = (item, entry)
            inputs_box.show_all()

        object_combo.connect("changed", rebuild_actions)
        action_combo.connect("changed", rebuild_inputs)
        visual_combo.connect("changed", reset_match_percentage)
        add_match_button.connect("clicked", add_match)
        for label, widget in (("Object", object_combo), ("Action", action_combo)):
            box.pack_start(Gtk.Label(label=label), False, False, 0); box.pack_start(widget, False, False, 0)
        box.pack_start(Gtk.Label(label="Action Inputs"), False, False, 0); box.pack_start(inputs_box, False, False, 0)
        group_entry = Gtk.Entry(); group_entry.set_placeholder_text("Composed step/group"); box.pack_start(Gtk.Label(label="Composed Step"), False, False, 0); box.pack_start(group_entry, False, False, 0)
        rebuild_actions(); rebuild_inputs(); dialog.show_all(); response = dialog.run()
        if response != Gtk.ResponseType.OK: dialog.destroy(); return
        component_id = object_combo.get_active_id(); action_id = action_combo.get_active_id(); group = group_entry.get_text().strip() or "Step"
        try:
            if action_id == "assert_match":
                variant_key = visual_combo.get_active_id()
                if not variant_key: raise ValueError("select an existing match image or add a new one")
                minimum_match_percentage = float(match_percentage.get_value())
                call = StepCall(
                    _next_node_id(self.plan.steps),
                    "gui.object.visual.assert",
                    inputs={
                        "component_id": component_id,
                        "variant_key": variant_key,
                        "minimum_match_percentage": minimum_match_percentage,
                    },
                    group=group,
                )
            else:
                definition = next(item for item in actions_for(self.repository.get(component_id)) if item.action_id == action_id)
                values = {}
                for name, (item, entry) in input_entries.items():
                    raw = entry.get_text().strip()
                    if not raw:
                        if item.required: raise ValueError("missing required input %s" % name)
                        if item.default is not None: values[name] = item.default
                        continue
                    try: values[name] = json.loads(raw)
                    except ValueError: values[name] = raw
                call = replace(definition.to_step_call(_next_node_id(self.plan.steps), component_id, values), group=group)
            self.plan = replace(self.plan, steps=(*self.plan.steps, call))
        except Exception as exc:
            dialog.destroy(); return self.error("Object Action", "%s: %s" % (type(exc).__name__, exc))
        dialog.destroy(); self.mark_dirty(); self.refresh_all(); self.set_status("Added %s on %s" % ("Assert Match" if action_id == "assert_match" else definition.name, component_id))

    def run_test(self):
        try:
            expanded = self._expanded(); issues = validate_plan(expanded, self.runtime_registry); issues.extend(validate_plan_components(expanded, self.repository))
            if issues: return self.error("Run Test", "\n".join(issues))
            prefs = AuthoringPreferences.load(); runs_dir = prefs.resolved_runs_dir(self.project); runs_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc: return self.error("Run Test", "%s: %s" % (type(exc).__name__, exc))
        self.run_button.set_sensitive(False); self.set_status("Running test…"); self.window.iconify()
        plan = self.plan; reusable = dict(self.reusable); repository = self.repository
        def worker():
            try: result = execute_plan(plan, LiveDesktopBackend(), runs_dir=runs_dir, component_repository=repository, reusable_steps=reusable)
            except Exception as exc: GLib.idle_add(self._run_finished, None, exc); return
            GLib.idle_add(self._run_finished, result, None)
        GLib.timeout_add(175, lambda: (threading.Thread(target=worker, name="automation-plan-window-run", daemon=True).start() or False))

    def _run_finished(self, result, error):
        self.window.deiconify(); self.window.present()
        launcher = getattr(self, "launching_window", None)
        launcher_window = getattr(launcher, "window", None)
        if launcher_window is not None:
            try:
                launcher_window.deiconify(); launcher_window.present()
            except Exception:
                pass
        return super()._run_finished(result, error)
