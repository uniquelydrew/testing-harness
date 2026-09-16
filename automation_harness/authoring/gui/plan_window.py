from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.authoring.action_catalog import actions_for
from automation_harness.authoring.gui.common import ArtifactWindow
from automation_harness.authoring.preferences_runtime import AuthoringPreferences
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.authoring.project_registry_service import save_plan_selection_to_project_registry
from automation_harness.authoring.step_registry import load_step_registry_resources
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_step_expansion import expand_reusable_steps
from automation_harness.core.reusable_step_snapshot import load_snapshotted_reusable_steps, snapshot_reusable_dependencies
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import embed_plan_repository, load_plan, repository_from_plan, save_plan, validate_plan, validate_plan_components
from automation_harness.models.plan import PlanVariableRef, StepCall
from automation_harness.runner.plan_execution import execute_plan


class TestPlanWindow(ArtifactWindow):
    title_prefix = "Automation Harness Test Plan"

    def __init__(self, path, *, project_context=None, opener=None):
        super().__init__(path, project_context=project_context, opener=opener)
        self.plan = load_plan(self.path)
        self.runtime_registry = default_step_registry()
        self.project = AuthoringProject.load(self.project_context) if self.project_context else None
        self.registry_resources = load_step_registry_resources(self.project.step_registries) if self.project and self.project.step_registries else None
        self.reusable = dict(self.registry_resources.steps) if self.registry_resources else load_snapshotted_reusable_steps(self.plan)
        self.repository = repository_from_plan(self.plan)
        if self.registry_resources:
            self.repository = self.repository.overlay(self.registry_resources.repository)

        self.button("Save", self.save)
        self.button("Validate", self.validate)
        self.run_button = self.button("Run Test", self.run_test)
        content = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.root.pack_start(content, True, True, 0)
        self._build_library(content)
        center_right = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        content.pack2(center_right, resize=True, shrink=False)
        self._build_flow(center_right)
        self._build_details(center_right)
        content.set_position(320); center_right.set_position(610)

        self._build_variables()
        self.refresh_all()

    def _build_library(self, parent):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        parent.pack1(box, resize=False, shrink=False)
        box.pack_start(Gtk.Label(label="ADD TO TEST"), False, False, 0)

        notebook = Gtk.Notebook()
        notebook.set_tab_pos(Gtk.PositionType.TOP)
        box.pack_start(notebook, True, True, 0)

        steps_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.library_search = Gtk.SearchEntry()
        self.library_search.set_placeholder_text("Reusable step")
        self.library_search.connect("search-changed", lambda *_args: self.refresh_library())
        steps_page.pack_start(self.library_search, False, False, 0)
        self.library_tree, self.library_store = self.list_tree((("Step", 170), ("ID", 220)))
        self.library_tree.connect("row-activated", lambda *_args: self.insert_reusable())
        self.library_tree.get_selection().connect("changed", lambda *_args: self.show_reusable_detail())
        steps_page.pack_start(self.scrolled(self.library_tree), True, True, 0)
        self.button("Insert Selected", self.insert_reusable, parent=steps_page)
        notebook.append_page(steps_page, Gtk.Label(label="Reusable Steps"))

        objects_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.object_search = Gtk.SearchEntry()
        self.object_search.set_placeholder_text("Object ID")
        self.object_search.connect("search-changed", lambda *_args: self.refresh_objects())
        objects_page.pack_start(self.object_search, False, False, 0)
        self.object_tree, self.object_store = self.list_tree((("Object", 260), ("Type", 140)))
        self.object_tree.connect("row-activated", lambda *_args: self.insert_object_action())
        self.object_tree.get_selection().connect("changed", lambda *_args: self.refresh_object_actions())
        objects_page.pack_start(self.scrolled(self.object_tree), True, True, 0)

        actions_label = Gtk.Label(label="AVAILABLE ACTIONS")
        actions_label.set_halign(Gtk.Align.START)
        objects_page.pack_start(actions_label, False, False, 0)
        self.object_action_tree, self.object_action_store = self.list_tree(
            (("Action", 150), ("Category", 110), ("Description", 220)),
        )
        self.object_action_tree.connect("row-activated", lambda *_args: self.insert_object_action())
        objects_page.pack_start(self.scrolled(self.object_action_tree), True, True, 0)
        self.button("Add Action", self.insert_object_action, parent=objects_page)
        notebook.append_page(objects_page, Gtk.Label(label="Objects"))

    def refresh_objects(self):
        selected_id = self.selected(self.object_tree, 0)
        self.object_store.clear()
        query = self.object_search.get_text().strip().casefold()
        for component_id, definition in sorted(self.repository.components.items()):
            text = "%s %s" % (component_id, definition.object_type.value)
            if query and query not in text.casefold():
                continue
            self.object_store.append((component_id, definition.object_type.value))
        if selected_id:
            iterator = self.object_store.get_iter_first()
            while iterator is not None:
                if self.object_store.get_value(iterator, 0) == selected_id:
                    self.object_tree.get_selection().select_iter(iterator)
                    break
                iterator = self.object_store.iter_next(iterator)
        self.refresh_object_actions()

    def refresh_object_actions(self):
        self.object_action_store.clear()
        component_id = self.selected(self.object_tree, 0)
        if not component_id or not self.repository.contains(component_id):
            return
        for action in actions_for(self.repository.get(component_id)):
            self.object_action_store.append((
                action.action_id,
                action.category,
                action.description or action.name,
            ))
        if self.object_action_store.get_iter_first() is not None:
            self.object_action_tree.get_selection().select_path(Gtk.TreePath.new_first())

    def insert_object_action(self):
        component_id = self.selected(self.object_tree, 0)
        if not component_id:
            return self.info("Objects", "Select an object first.")
        action_id = self.selected(self.object_action_tree, 0)
        if not action_id:
            return self.info("Actions", "The selected object has no available action.")
        handler = getattr(self, "add_object_action", None)
        if handler is None:
            return
        handler(component_id=component_id, action_id=action_id)

    def _build_flow(self, parent):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); parent.pack1(box, resize=True, shrink=False)
        row = Gtk.Box(spacing=6); box.pack_start(row, False, False, 0)
        row.pack_start(Gtk.Label(label="TEST FLOW"), False, False, 0)
        self.flow_tree, self.flow_store = self.list_tree((("Group", 150), ("Node", 110), ("Step", 250), ("Inputs", 250)))
        self.flow_tree.get_selection().connect("changed", lambda *_args: self.show_flow_detail())
        self.flow_tree.connect("row-activated", lambda *_args: self.edit_selected_call())
        self.flow_tree.connect("button-press-event", self._flow_button_press)
        self.flow_tree.connect("key-press-event", self._flow_key_press)
        self.flow_tree.set_reorderable(True)
        self.flow_store.connect("rows-reordered", lambda *_args: self._flow_reordered())
        box.pack_start(self.scrolled(self.flow_tree), True, True, 0)

    def _build_variables(self):
        frame = Gtk.Frame(label="VARIABLES")
        frame.set_margin_top(4)
        self.root.pack_start(frame, False, True, 0)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_border_width(4)
        frame.add(box)
        self.variables_tree, self.variables_store = self.list_tree((("Name", 220), ("Value", 420)))
        self.variables_tree.set_headers_visible(True)
        self.variables_tree.connect("row-activated", lambda *_args: self.edit_selected_variable())
        box.pack_start(self.scrolled(self.variables_tree), True, True, 0)
        actions = Gtk.Box(spacing=4)
        self.button("Add", self.add_variable, parent=actions)
        self.button("Edit", self.edit_selected_variable, parent=actions)
        self.button("Remove", self.remove_selected_variable, parent=actions)
        box.pack_start(actions, False, False, 0)

    def _flow_button_press(self, _tree, event):
        if event.button != 3:
            return False
        path_info = self.flow_tree.get_path_at_pos(int(event.x), int(event.y))
        if path_info is None:
            return False
        path, _column, _cell_x, _cell_y = path_info
        self.flow_tree.get_selection().select_path(path)
        self._show_flow_menu(int(event.button), event.time)
        return True

    def _flow_key_press(self, _tree, event):
        if event.keyval == Gdk.KEY_Delete:
            self.remove_selected()
            return True
        return False

    def _flow_reordered(self):
        ordered_ids = []
        iterator = self.flow_store.get_iter_first()
        while iterator is not None:
            ordered_ids.append(self.flow_store.get_value(iterator, 1))
            iterator = self.flow_store.iter_next(iterator)
        by_id = {call.node_id: call for call in self.plan.steps}
        if set(ordered_ids) != set(by_id):
            return
        reordered = tuple(by_id[node_id] for node_id in ordered_ids)
        if reordered != tuple(self.plan.steps):
            self.plan = replace(self.plan, steps=reordered)
            self.mark_dirty()
            self.set_status("%d authored calls • drag order updated" % len(self.plan.steps))

    def _show_flow_menu(self, button=3, event_time=0):
        node_id = self.selected(self.flow_tree, 1)
        if not node_id:
            return
        menu = Gtk.Menu()
        def item(label, callback, sensitive=True):
            entry = Gtk.MenuItem(label=label)
            entry.set_sensitive(sensitive)
            entry.connect("activate", lambda *_args: callback())
            menu.append(entry)
            return entry
        item("Edit Call", self.edit_selected_call)
        item("Move Up", lambda: self.move_selected(-1))
        item("Move Down", lambda: self.move_selected(1))
        call = next((value for value in self.plan.steps if value.node_id == node_id), None)
        item("Save as Reusable Step", self.save_group_to_registry, bool(call and call.group))
        item("Remove", self.remove_selected)
        menu.show_all()
        menu.popup_at_pointer(None)

    def _variable_value_text(self, value):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))

    def refresh_variables(self):
        self.variables_store.clear()
        for name, value in sorted(dict(self.plan.variables).items()):
            self.variables_store.append((str(name), self._variable_value_text(value)))

    def _selected_variable_name(self):
        return self.selected(self.variables_tree, 0)

    def add_variable(self):
        name = self.ask_text("Add Variable", "Name:")
        if not name:
            return
        if name in self.plan.variables:
            return self.error("Add Variable", "A variable named %s already exists." % name)
        raw = self.ask_text("Add Variable", "Value (JSON when applicable):", "")
        if raw is None:
            return
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        variables = dict(self.plan.variables)
        variables[name] = value
        self.plan = replace(self.plan, variables=variables)
        self.mark_dirty()
        self.refresh_variables()

    def edit_selected_variable(self):
        name = self._selected_variable_name()
        if not name:
            return self.info("Variables", "Select a variable first.")
        raw = self.ask_text("Edit Variable", "Value (JSON when applicable):", self._variable_value_text(self.plan.variables[name]))
        if raw is None:
            return
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        variables = dict(self.plan.variables)
        variables[name] = value
        self.plan = replace(self.plan, variables=variables)
        self.mark_dirty()
        self.refresh_variables()

    def remove_selected_variable(self):
        name = self._selected_variable_name()
        if not name:
            return
        if not self.confirm("Remove Variable", "Remove %s from this Test Plan?" % name):
            return
        variables = dict(self.plan.variables)
        variables.pop(name, None)
        self.plan = replace(self.plan, variables=variables)
        self.mark_dirty()
        self.refresh_variables()

    def _build_details(self, parent):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); parent.pack2(box, resize=True, shrink=False)
        box.pack_start(Gtk.Label(label="DETAILS"), False, False, 0)
        self.detail = Gtk.TextView(); self.detail.set_editable(False); self.detail.set_monospace(True)
        box.pack_start(self.scrolled(self.detail), True, True, 0)
        self.button("Edit Selected Call", self.edit_selected_call, parent=box)

    def refresh_all(self):
        self.refresh_library(); self.refresh_objects(); self.refresh_flow()
        self.refresh_variables()

    def refresh_library(self):
        self.library_store.clear(); query = self.library_search.get_text().strip().casefold()
        for step_id, definition in sorted(self.reusable.items()):
            text = " ".join((definition.name, step_id, definition.description)).casefold()
            if query and query not in text: continue
            self.library_store.append((definition.name, step_id))

    def refresh_flow(self):
        self.flow_store.clear()
        for call in self.plan.steps:
            self.flow_store.append((call.group or "Ungrouped", call.node_id, call.step_id, json.dumps(_encode(call.inputs), separators=(",", ":"))))
        self.set_status("%d authored calls • %d reusable definitions" % (len(self.plan.steps), len(self.reusable)))
        self.show_flow_detail()

    def show_reusable_detail(self):
        step_id = self.selected(self.library_tree, 1)
        if not step_id: return
        definition = self.reusable[step_id]
        lines = [
            "REUSABLE STEP",
            "",
            "Name: %s" % definition.name,
            "ID: %s" % step_id,
            "Description: %s" % (definition.description or "—"),
            "Internal calls: %d" % len(definition.plan.steps),
            "",
            "Inputs: %s" % (", ".join(definition.inputs) if definition.inputs else "none"),
            "Outputs: %s" % (", ".join(definition.outputs) if definition.outputs else "none"),
        ]
        self.detail.get_buffer().set_text("\n".join(lines))

    def show_flow_detail(self):
        node_id = self.selected(self.flow_tree, 1)
        if not node_id: return
        call = next(item for item in self.plan.steps if item.node_id == node_id)
        self.detail.get_buffer().set_text("\n".join((
            "TEST FLOW CALL",
            "",
            "Step: %s" % call.step_id,
            "Node: %s" % call.node_id,
            "Group: %s" % (call.group or "Ungrouped"),
            "Inputs: %s" % json.dumps(_encode(call.inputs), ensure_ascii=False, default=str),
            "Outputs: %s" % (", ".join("%s → %s" % item for item in call.outputs.items()) if call.outputs else "none"),
            "Depends on: %s" % (", ".join(call.depends_on) if call.depends_on else "none"),
            "",
            call.description or "",
        )))

    def insert_reusable(self):
        step_id = self.selected(self.library_tree, 1)
        if not step_id: return self.info("Step Library", "Select a reusable step first.")
        definition = self.reusable[step_id]
        configured = self._configure_contract(definition)
        if configured is None: return
        inputs, outputs = configured
        group = self.ask_text("Insert Registry Step", "Composed step/group:", definition.name) or definition.name
        call = StepCall(_next_node_id(self.plan.steps), step_id, inputs=inputs, outputs=outputs, group=group)
        self.plan = replace(self.plan, steps=(*self.plan.steps, call)); self.mark_dirty(); self.refresh_all()

    def _configure_contract(self, definition):
        dialog = Gtk.Dialog(title="Insert %s" % definition.name, transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Insert", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        entries = {}
        for name, metadata in definition.inputs.items():
            row = Gtk.Box(spacing=6); label = Gtk.Label(label=name); label.set_size_request(180, -1); entry = Gtk.Entry()
            if "default" in metadata: entry.set_text(json.dumps(metadata["default"], default=str))
            entry.set_placeholder_text("JSON or ${plan.variable}")
            row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0); box.pack_start(row, False, False, 0); entries[name] = (metadata, entry)
        outputs = {}
        for name in definition.outputs:
            row = Gtk.Box(spacing=6); label = Gtk.Label(label="Output " + name); label.set_size_request(180, -1); entry = Gtk.Entry(); entry.set_text(name)
            row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0); box.pack_start(row, False, False, 0); outputs[name] = entry
        dialog.show_all(); response = dialog.run()
        if response != Gtk.ResponseType.OK: dialog.destroy(); return None
        values = {}
        for name, (metadata, entry) in entries.items():
            raw = entry.get_text().strip()
            if not raw:
                if metadata.get("required") and "default" not in metadata: dialog.destroy(); return self.error("Registry Step", "Missing required input %s" % name)
                continue
            if raw.startswith("${") and raw.endswith("}"): values[name] = PlanVariableRef(raw[2:-1].strip())
            else:
                try: values[name] = json.loads(raw)
                except ValueError: values[name] = raw
        bindings = {name: entry.get_text().strip() for name, entry in outputs.items() if entry.get_text().strip()}
        dialog.destroy(); return values, bindings

    def edit_selected_call(self):
        node_id = self.selected(self.flow_tree, 1)
        if not node_id: return
        call = next(item for item in self.plan.steps if item.node_id == node_id)
        dialog = Gtk.Dialog(title="Edit Call", transient_for=self.window, modal=True); dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Apply", Gtk.ResponseType.OK)
        text = Gtk.TextView(); text.set_monospace(True); text.get_buffer().set_text(json.dumps(call.to_dict(), indent=2, default=str)); scroll = self.scrolled(text); scroll.set_size_request(700, 520); dialog.get_content_area().pack_start(scroll, True, True, 0)
        dialog.show_all(); response = dialog.run(); buffer = text.get_buffer(); raw = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True); dialog.destroy()
        if response != Gtk.ResponseType.OK: return
        try:
            payload = json.loads(raw); updated = replace(call, group=str(payload.get("group", call.group)), inputs=_decode(payload.get("inputs", {})), outputs={str(k): str(v) for k, v in payload.get("outputs", {}).items()}, depends_on=tuple(payload.get("depends_on", call.depends_on)))
            self.plan = replace(self.plan, steps=tuple(updated if item.node_id == node_id else item for item in self.plan.steps))
        except Exception as exc: return self.error("Edit Call", str(exc))
        self.mark_dirty(); self.refresh_all()

    def remove_selected(self):
        node_id = self.selected(self.flow_tree, 1)
        if node_id: self.plan = replace(self.plan, steps=tuple(item for item in self.plan.steps if item.node_id != node_id)); self.mark_dirty(); self.refresh_all()

    def move_selected(self, offset):
        node_id = self.selected(self.flow_tree, 1)
        if not node_id: return
        steps = list(self.plan.steps); index = next(i for i, item in enumerate(steps) if item.node_id == node_id); target = index + offset
        if target < 0 or target >= len(steps): return
        steps[index], steps[target] = steps[target], steps[index]; self.plan = replace(self.plan, steps=tuple(steps)); self.mark_dirty(); self.refresh_all()

    def _expanded(self):
        return expand_reusable_steps(self.plan, self.reusable) if self.reusable else self.plan

    def validate(self):
        try:
            expanded = self._expanded(); issues = validate_plan(expanded, self.runtime_registry); issues.extend(validate_plan_components(expanded, self.repository))
        except Exception as exc: issues = ["%s: %s" % (type(exc).__name__, exc)]
        if issues: return self.error("Plan Validation", "\n".join(issues))
        self.info("Plan Validation", "Plan is structurally valid after reusable-step expansion.")

    def save(self):
        try:
            portable = snapshot_reusable_dependencies(self.plan, self.reusable, self.repository) if self.reusable else self.plan
            portable = embed_plan_repository(portable, self.repository); save_plan(portable, self.path); self.plan = portable
            if self.project_context:
                project = AuthoringProject.load(self.project_context).with_test_plan(self.path); save_authoring_project(self.project_context, project); self.project = project
        except Exception as exc: return self.error("Save Test Plan", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(False); self.set_status("Saved test plan")

    def save_group_to_registry(self):
        if not self.project_context: return self.info("Step Registry", "Open this Test Plan through a Project to save reusable compositions into Project Registries.")
        node_id = self.selected(self.flow_tree, 1)
        if not node_id: return self.info("Step Registry", "Select a Test Flow call first.")
        selected = next(item for item in self.plan.steps if item.node_id == node_id)
        if not selected.group: return self.info("Step Registry", "The selected call is not part of a composed group.")
        project = AuthoringProject.load(self.project_context)
        if not project.step_registries: return self.info("Step Registry", "Create or add a Step Registry from the Project window first.")
        dialog = Gtk.Dialog(title="Save as Reusable Step", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(6)
        box.set_border_width(10)
        step_id_entry = Gtk.Entry()
        step_id_entry.set_placeholder_text("Reusable step ID")
        name_entry = Gtk.Entry()
        name_entry.set_text(selected.group)
        registry_combo = Gtk.ComboBoxText()
        for registry_path in project.step_registries:
            registry_combo.append(str(registry_path), registry_path.stem)
        registry_combo.set_active(0)
        for label, widget in (("Step ID", step_id_entry), ("Display name", name_entry), ("Registry", registry_combo)):
            box.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
            box.pack_start(widget, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        step_id = step_id_entry.get_text().strip()
        name = name_entry.get_text().strip()
        registry_value = registry_combo.get_active_id()
        dialog.destroy()
        if response != Gtk.ResponseType.OK or not step_id or not name or not registry_value:
            return
        registry_path = Path(registry_value)
        try:
            project, _registry = save_plan_selection_to_project_registry(
                self.project_context,
                self.plan,
                source_repository=self.repository,
                registry_path=registry_path,
                step_id=step_id,
                name=name,
                group=selected.group,
            )
            self.project = project
            self.registry_resources = load_step_registry_resources(project.step_registries)
            self.reusable = dict(self.registry_resources.steps)
            self.repository = repository_from_plan(self.plan).overlay(self.registry_resources.repository)
        except Exception as exc:
            return self.error("Save as Reusable Step", "%s: %s" % (type(exc).__name__, exc))
        self.refresh_all()
        self.set_status("Saved %s to %s" % (name, registry_path.name))

    def run_test(self):
        try:
            expanded = self._expanded(); issues = validate_plan(expanded, self.runtime_registry); issues.extend(validate_plan_components(expanded, self.repository))
            if issues: return self.error("Run Test", "\n".join(issues))
            prefs = AuthoringPreferences.load(); runs_dir = prefs.resolved_runs_dir(self.project); runs_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc: return self.error("Run Test", "%s: %s" % (type(exc).__name__, exc))
        self.run_button.set_sensitive(False); self.set_status("Running test…")
        plan = self.plan; reusable = dict(self.reusable); repository = self.repository
        def worker():
            try: result = execute_plan(plan, LiveDesktopBackend(), runs_dir=runs_dir, component_repository=repository, reusable_steps=reusable)
            except Exception as exc: GLib.idle_add(self._run_finished, None, exc); return
            GLib.idle_add(self._run_finished, result, None)
        threading.Thread(target=worker, name="automation-plan-window-run", daemon=True).start()

    def _run_finished(self, result, error):
        self.run_button.set_sensitive(True)
        if error is not None: self.set_status("Run failed"); self.error("Run Test", "%s: %s" % (type(error).__name__, error)); return False
        self.set_status("Run %s" % ("passed" if result.exit_code == 0 else "failed")); message = "Passed: %s\nFailed: %s\nArtifacts: %s" % (result.passed, result.failed, result.artifact_dir)
        (self.info if result.exit_code == 0 else self.error)("Run Test", message); return False


def _next_node_id(steps):
    used = {item.node_id for item in steps}; index = 1
    while "step-%03d" % index in used: index += 1
    return "step-%03d" % index


def _encode(value):
    if isinstance(value, PlanVariableRef): return {"$var": value.path}
    if isinstance(value, dict): return {key: _encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)): return [_encode(item) for item in value]
    return value


def _decode(value):
    if isinstance(value, dict):
        if set(value) == {"$var"}: return PlanVariableRef(str(value["$var"]))
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list): return [_decode(item) for item in value]
    return value