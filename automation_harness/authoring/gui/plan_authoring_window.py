from __future__ import annotations

import json
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.action_catalog import actions_for
from automation_harness.authoring.gui.plan_window import TestPlanWindow, _next_node_id
from automation_harness.authoring.plan_repository import assign_repository, assigned_repository_path, load_authoring_repository
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.core.reusable_step_snapshot import snapshot_reusable_dependencies
from automation_harness.core.test_plan import embed_plan_repository, repository_from_plan, save_plan
from automation_harness.formats import REPOSITORY_SUFFIX


class TestPlanAuthoringWindow(TestPlanWindow):
    """Concrete test composition workflow layered on the Test Plan document window."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.assigned_repository_path = assigned_repository_path(self.plan, self.path)
        if self.assigned_repository_path is not None and self.assigned_repository_path.exists():
            assigned, _path = load_authoring_repository(self.plan, self.path)
            self.repository = repository_from_plan(self.plan).overlay(assigned)
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
        self.button("Add Object Action", self.add_object_action)
        self.button("Assign Object Repository", self.assign_object_repository)
        self.button("Open Object Repository", self.open_assigned_repository)
        self.window.show_all()

    def assign_object_repository(self):
        selected = self.choose_file(title="Assign Object Repository", suffix=REPOSITORY_SUFFIX)
        if selected is None:
            return
        try:
            selected = selected.resolve()
            if not selected.is_file():
                raise ValueError("object repository does not exist")
            assigned = __import__("automation_harness.core.component_repository", fromlist=["ComponentRepository"]).ComponentRepository.load((selected,))
            self.plan = assign_repository(self.plan, self.path, selected)
            self.assigned_repository_path = selected
            self.repository = repository_from_plan(self.plan).overlay(assigned)
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
            if self.project_context:
                project = AuthoringProject.load(self.project_context).with_object_repository(selected)
                save_authoring_project(self.project_context, project); self.project = project
        except Exception as exc:
            return self.error("Assign Object Repository", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(); self.refresh_all(); self.set_status("Assigned object repository: %s" % selected.name)

    def open_assigned_repository(self):
        path = assigned_repository_path(self.plan, self.path)
        if path is None:
            return self.info("Object Repository", "This Test Plan does not currently have an assigned Object Repository.")
        return self.open_artifact(path, project_context=self.project_context)

    def save(self):
        """Persist direct objects first, then add transitive Registry dependencies."""
        try:
            portable = embed_plan_repository(self.plan, self.repository)
            if self.reusable:
                portable = snapshot_reusable_dependencies(portable, self.reusable, self.repository)
            save_plan(portable, self.path)
            self.plan = portable
            if self.project_context:
                project = AuthoringProject.load(self.project_context).with_test_plan(self.path)
                repository_path = assigned_repository_path(self.plan, self.path)
                if repository_path is not None:
                    project = project.with_object_repository(repository_path)
                save_authoring_project(self.project_context, project)
                self.project = project
        except Exception as exc:
            return self.error("Save Test Plan", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(False); self.set_status("Saved portable test plan")

    def add_object_action(self):
        if not self.repository.components:
            return self.info(
                "Object Action",
                "No objects are available. Assign or capture objects in an Object Repository first.",
            )
        dialog = Gtk.Dialog(title="Add Object Action", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)

        object_combo = Gtk.ComboBoxText()
        for component_id in sorted(self.repository.components):
            object_combo.append(component_id, component_id)
        object_combo.set_active(0)
        action_combo = Gtk.ComboBoxText()
        inputs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        input_entries = {}

        def rebuild_actions(*_args):
            action_combo.remove_all()
            component_id = object_combo.get_active_id()
            if not component_id:
                return
            definitions = actions_for(self.repository.get(component_id))
            for definition in definitions:
                action_combo.append(definition.action_id, "%s — %s" % (definition.name, definition.category))
            if definitions:
                action_combo.set_active(0)

        def rebuild_inputs(*_args):
            for child in inputs_box.get_children(): child.destroy()
            input_entries.clear()
            component_id = object_combo.get_active_id(); action_id = action_combo.get_active_id()
            if not component_id or not action_id: return
            definition = next(item for item in actions_for(self.repository.get(component_id)) if item.action_id == action_id)
            for item in definition.inputs:
                row = Gtk.Box(spacing=6); label = Gtk.Label(label=item.name + (" *" if item.required else "")); label.set_size_request(170, -1); label.set_xalign(0)
                entry = Gtk.Entry(); entry.set_placeholder_text(item.description or item.value_type)
                if item.default is not None: entry.set_text(json.dumps(item.default, default=str))
                row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0); inputs_box.pack_start(row, False, False, 0); input_entries[item.name] = (item, entry)
            inputs_box.show_all()

        object_combo.connect("changed", rebuild_actions); action_combo.connect("changed", rebuild_inputs)
        box.pack_start(Gtk.Label(label="Object"), False, False, 0); box.pack_start(object_combo, False, False, 0)
        box.pack_start(Gtk.Label(label="Action"), False, False, 0); box.pack_start(action_combo, False, False, 0)
        box.pack_start(Gtk.Label(label="Action Inputs"), False, False, 0); box.pack_start(inputs_box, False, False, 0)
        group_entry = Gtk.Entry(); group_entry.set_placeholder_text("Composed step/group"); box.pack_start(Gtk.Label(label="Composed Step"), False, False, 0); box.pack_start(group_entry, False, False, 0)
        rebuild_actions(); rebuild_inputs(); dialog.show_all(); response = dialog.run()
        if response != Gtk.ResponseType.OK: dialog.destroy(); return
        component_id = object_combo.get_active_id(); action_id = action_combo.get_active_id(); group = group_entry.get_text().strip() or "Step"
        try:
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
        dialog.destroy(); self.mark_dirty(); self.refresh_all(); self.set_status("Added %s on %s" % (definition.name, component_id))
