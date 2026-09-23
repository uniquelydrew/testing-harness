from __future__ import annotations

import json
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from automation_harness.authoring.action_catalog import actions_for
from automation_harness.authoring.gui.action_widgets import (
    create_action_input_widget,
    interaction_actions,
    read_action_input,
)
from automation_harness.authoring.gui.plan_window import TestPlanWindow, _next_node_id
from automation_harness.authoring.plan_repository import (
    assign_repository,
    assigned_repository_path,
    load_authoring_repository,
    merge_repository_or,
)
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.core.component_repository import ComponentRepository
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
        self.objects_button = self.button("Objects", self.show_objects_menu)
        self.window.show_all()

    def show_objects_menu(self):
        self.refresh_objects()
        menu = Gtk.Menu()

        def add_item(label, callback, sensitive=True):
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(sensitive)
            item.connect("activate", lambda *_args: callback())
            menu.append(item)

        add_item("Add Object Action", self.add_object_action)
        menu.append(Gtk.SeparatorMenuItem())
        add_item("Assign Repository…", self.assign_object_repository)
        add_item("Merge Repository…", self.merge_assign_central_repository)
        add_item(
            "Open Repository",
            self.open_assigned_repository,
            assigned_repository_path(self.plan, self.path) is not None,
        )
        menu.show_all()
        menu.popup_at_widget(self.objects_button, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)

    def assign_object_repository(self):
        selected = self.choose_file(title="Assign Object Repository", suffix=REPOSITORY_SUFFIX)
        if selected is None:
            return
        try:
            selected = selected.resolve()
            if not selected.is_file():
                raise ValueError("object repository does not exist")
            assigned = ComponentRepository.load((selected,))
            self.plan = assign_repository(self.plan, self.path, selected)
            self.assigned_repository_path = selected
            self._assigned_repository_token = None
            self.repository = repository_from_plan(self.plan).overlay(assigned)
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
            if self.project_context:
                project = AuthoringProject.load(self.project_context).with_object_repository(selected)
                save_authoring_project(self.project_context, project)
                self.project = project
        except Exception as exc:
            return self.error("Assign Object Repository", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty()
        self.refresh_all()
        self.set_status("Assigned object repository: %s" % selected.name)

    def merge_assign_central_repository(self):
        selected = self.choose_file(title="Merge into Central Object Repository", suffix=REPOSITORY_SUFFIX)
        if selected is None:
            return
        try:
            selected = selected.resolve()
            if not selected.is_file():
                raise ValueError("central object repository does not exist")

            current_path = assigned_repository_path(self.plan, self.path)
            if current_path is not None and current_path.exists() and current_path.resolve() != selected:
                source = ComponentRepository.load((current_path.resolve(),))
                source_label = current_path.name
            elif current_path is None:
                # An older/standalone plan may have portable embedded objects
                # without an authoring repository assignment. Those objects are
                # its effective local OR and must not be lost during migration.
                source = repository_from_plan(self.plan)
                source_label = "%s embedded objects" % self.path.name
            else:
                source = ComponentRepository({})
                source_label = selected.name

            central = ComponentRepository.load((selected,))
            if source.components:
                if not self.confirm(
                    "Merge and Assign Repository",
                    "Merge %d object(s) from %s into %s, then make %s the Test Plan's authoring repository?\n\n"
                    "Compatible definitions are combined as OR resolver alternatives. The source repository is not deleted."
                    % (len(source.components), source_label, selected.name, selected.name),
                ):
                    return
                central, stats = merge_repository_or(central, source)
                central.save(selected)
            else:
                stats = {"added": 0, "merged": 0, "unchanged": 0}

            # Assignment happens only after a successful merge/save. From this
            # point onward recording's assigned_repository_path lookup resolves
            # to the central file, making it the destination for new captures.
            self.plan = assign_repository(self.plan, self.path, selected)
            self.assigned_repository_path = selected
            self._assigned_repository_token = None
            self.repository = repository_from_plan(self.plan).overlay(central)
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)

            if self.project_context:
                project = AuthoringProject.load(self.project_context).with_object_repository(selected)
                save_authoring_project(self.project_context, project)
                self.project = project
        except Exception as exc:
            return self.error("Merge / Assign Central Repository", "%s: %s" % (type(exc).__name__, exc))

        self.mark_dirty()
        self.refresh_all()
        self.set_status(
            "Central repository assigned: %s — %d added, %d OR-merged, %d already equivalent; new recordings write here"
            % (selected.name, stats["added"], stats["merged"], stats["unchanged"])
        )

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
        self.mark_dirty(False)
        self.set_status("Saved portable test plan")

    def add_object_action(self, component_id=None, action_id=None):
        self.refresh_objects()
        if not self.repository.components:
            return self.info(
                "Object Action",
                "No objects are available. Assign or capture objects in an Object Repository first.",
            )
        dialog = Gtk.Dialog(title="Add Object Action", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)

        object_combo = Gtk.ComboBoxText()
        for known_component_id in sorted(self.repository.components):
            object_combo.append(known_component_id, known_component_id)
        if component_id and component_id in self.repository.components:
            object_combo.set_active_id(component_id)
        else:
            object_combo.set_active(0)
        action_combo = Gtk.ComboBoxText()
        preferred_action_id = action_id
        inputs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        input_entries = {}

        def rebuild_actions(*_args):
            action_combo.remove_all()
            component_id = object_combo.get_active_id()
            if not component_id:
                return
            definitions = interaction_actions(self.repository.get(component_id))
            for definition in definitions:
                action_combo.append(definition.action_id, "%s — %s" % (definition.name, definition.category))
            if definitions:
                if preferred_action_id and any(item.action_id == preferred_action_id for item in definitions):
                    action_combo.set_active_id(preferred_action_id)
                else:
                    action_combo.set_active(0)

        def rebuild_inputs(*_args):
            for child in inputs_box.get_children():
                child.destroy()
            input_entries.clear()
            component_id = object_combo.get_active_id()
            action_id = action_combo.get_active_id()
            if not component_id or not action_id:
                return
            component = self.repository.get(component_id)
            definition = next(
                item for item in interaction_actions(component)
                if item.action_id == action_id
            )
            for item in definition.inputs:
                row = Gtk.Box(spacing=6)
                label = Gtk.Label(label=item.name + (" *" if item.required else ""))
                label.set_size_request(170, -1)
                label.set_xalign(0)
                entry = create_action_input_widget(item, component)
                row.pack_start(label, False, False, 0)
                row.pack_start(entry, True, True, 0)
                inputs_box.pack_start(row, False, False, 0)
                input_entries[item.name] = (item, entry)
            inputs_box.show_all()

        object_combo.connect("changed", rebuild_actions)
        action_combo.connect("changed", rebuild_inputs)
        box.pack_start(Gtk.Label(label="Object"), False, False, 0)
        box.pack_start(object_combo, False, False, 0)
        box.pack_start(Gtk.Label(label="Action"), False, False, 0)
        box.pack_start(action_combo, False, False, 0)
        box.pack_start(Gtk.Label(label="Action Inputs"), False, False, 0)
        box.pack_start(inputs_box, False, False, 0)
        group_entry = Gtk.Entry()
        group_entry.set_placeholder_text("Composed step/group")
        box.pack_start(Gtk.Label(label="Composed Step"), False, False, 0)
        box.pack_start(group_entry, False, False, 0)
        rebuild_actions()
        rebuild_inputs()
        dialog.show_all()
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        component_id = object_combo.get_active_id()
        action_id = action_combo.get_active_id()
        group = group_entry.get_text().strip() or "Step"
        try:
            definition = next(
                item for item in interaction_actions(self.repository.get(component_id))
                if item.action_id == action_id
            )
            values = {}
            for name, (item, entry) in input_entries.items():
                present, value = read_action_input(item, entry)
                if not present:
                    if item.required:
                        raise ValueError("missing required input %s" % name)
                    continue
                values[name] = value
            call = replace(definition.to_step_call(_next_node_id(self.plan.steps), component_id, values), group=group)
            self.plan = replace(self.plan, steps=(*self.plan.steps, call))
        except Exception as exc:
            dialog.destroy()
            return self.error("Object Action", "%s: %s" % (type(exc).__name__, exc))
        dialog.destroy()
        self.mark_dirty()
        self.refresh_all()
        self.set_status("Added %s on %s" % (definition.name, component_id))
