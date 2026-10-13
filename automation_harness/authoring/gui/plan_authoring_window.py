from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

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
from automation_harness.authoring.plan_open import inspect_plan_open, PlanOpenState
from automation_harness.authoring.step_order import insert_calls
from automation_harness.authoring.plan_repository import (
    assign_repositories,
    assigned_repository_path,
    load_repository_set,
    merge_repository_or,
)
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_migration import reconcile_object, persist_migration, stage_legacy_migration, detect_legacy_objects
from automation_harness.core.repository_scope import RepositoryAssociation, RepositoryScope
from automation_harness.core.reusable_step_snapshot import snapshot_reusable_dependencies
from automation_harness.core.test_plan import embed_plan_repository, repository_from_plan, save_plan
from automation_harness.formats import REPOSITORY_SUFFIX


class TestPlanAuthoringWindow(TestPlanWindow):
    """Concrete test composition workflow layered on the Test Plan document window."""

    def __init__(self, *args, **kwargs):
        # Preflight before TestPlanWindow can compose conflicting repositories.
        path = args[0] if args else kwargs["path"]
        self.open_result = inspect_plan_open(path)
        legacy = self.open_result.state is PlanOpenState.MIGRATION_REQUIRED
        kwargs["preloaded_plan"] = self.open_result.plan
        kwargs["defer_repository"] = legacy
        super().__init__(*args, **kwargs)
        self.assigned_repository_path = assigned_repository_path(self.plan, self.path)
        if not legacy and self.assigned_repository_path is not None and self.assigned_repository_path.exists():
            assigned = load_repository_set(self.plan, self.path).compose()
            self.repository = assigned
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
        self.objects_button = self.button("Objects", self.show_objects_menu)
        self.window.show_all()
        if not self._detect_and_migrate_legacy_on_open():
            self.window.destroy()

    def _detect_and_migrate_legacy_on_open(self):
        """Detect immediately after loading, before normal editing begins."""
        if not self.plan.objects:
            return True
        if not self.ensure_repository_interactive():
            self.info("Legacy Plan", "Repository assignment is required before legacy objects can be migrated.")
            return False
        try:
            destination = assigned_repository_path(self.plan, self.path)
            repository = ComponentRepository.load((destination,))
            safe, conflicts = detect_legacy_objects(self.plan, repository)
            if conflicts:
                self.error(
                    "Legacy Object Reconciliation Required",
                    "Legacy definitions conflict with the assigned repository. "
                    "Use Objects → Migrate Plan Objects to resolve them.\\n\\n"
                    + "\\n".join(conflicts),
                )
                self.migrate_plan_objects()
                if not self.plan.objects:
                    return True
                return False
            if not self.confirm(
                "Migrate Legacy Objects",
                "Detected %d legacy object(s) in this plan. Move them into %s "
                "before editing? Cancel preserves the original files."
                % (len(safe), destination.name),
            ):
                return False
            migrated, updated_repository = stage_legacy_migration(self.plan, repository)
            persist_migration(self.path, destination, migrated, updated_repository)
            self.plan = migrated
            self.repository = updated_repository
            self._assigned_repository_token = None
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
            self.refresh_all()
            self.mark_dirty(False)
            self.set_status("Migrated %d legacy objects on open" % len(safe))
            return True
        except Exception as exc:
            self.error("Legacy Migration", "%s: %s" % (type(exc).__name__, exc))
            return False

    def show_objects_menu(self):
        self.refresh_objects()
        menu = Gtk.Menu()

        def add_item(label, callback, sensitive=True):
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(sensitive)
            item.connect("activate", lambda *_args: callback())
            menu.append(item)

        add_item("Add Object Action", self.add_object_action)
        add_item("Migrate Plan Objects…", self.migrate_plan_objects, bool(self.plan.objects))
        menu.append(Gtk.SeparatorMenuItem())
        add_item("Assign Repository…", self.assign_object_repository)
        add_item("Create Repository…", self.create_object_repository)
        add_item("Merge Repository…", self.merge_assign_central_repository)
        add_item(
            "Open Repository",
            self.open_assigned_repository,
            assigned_repository_path(self.plan, self.path) is not None,
        )
        menu.show_all()
        menu.popup_at_widget(self.objects_button, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)


    def migrate_plan_objects(self):
        """Explicit ownership transfer; no repository snapshots remain in plan."""
        destination = assigned_repository_path(self.plan, self.path)
        if destination is None or not destination.is_file():
            return self.error("Migrate Plan Objects", "Assign an existing Object Repository first.")
        try:
            repository = ComponentRepository.load((destination,))
            dialog = Gtk.Dialog(title="Migrate Plan Objects", transient_for=self.window, modal=True)
            dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Migrate", Gtk.ResponseType.OK)
            area = dialog.get_content_area()
            area.set_spacing(8)
            area.set_border_width(10)
            label = Gtk.Label(label="Select a plan-local object to transfer to the assigned repository.")
            label.set_line_wrap(True)
            area.pack_start(label, False, False, 0)
            objects = Gtk.ComboBoxText()
            for name in sorted(self.plan.objects):
                objects.append(name, name)
            objects.set_active(0)
            area.pack_start(objects, False, False, 0)
            mode = Gtk.ComboBoxText()
            for key, caption in (("plan", "A — Plan definition"),
                                 ("repository", "B — Repository definition"),
                                 ("combine", "Combine properties")):
                mode.append(key, caption)
            mode.set_active(0)
            area.pack_start(mode, False, False, 0)
            field_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            area.pack_start(field_box, True, True, 0)
            field_choices = {}

            def refresh_fields(*_args):
                for child in field_box.get_children():
                    field_box.remove(child)
                    child.destroy()
                field_choices.clear()
                name = objects.get_active_id()
                if not name or mode.get_active_id() != "combine":
                    dialog.show_all()
                    return
                source = self.plan.objects[name]
                matches = [item for item in repository.components.values()
                           if item.object_id == source.get("object_id") or item.component_id == name]
                if len(matches) != 1:
                    field_box.pack_start(Gtk.Label(label="Combine requires one unambiguous matching OR object."), False, False, 0)
                    dialog.show_all()
                    return
                other = repository.to_document()["components"][matches[0].component_id]
                for key in sorted(set(source) | set(other)):
                    if key in ("object_id", "owner_object_id") or source.get(key) == other.get(key):
                        continue
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                    field_label = Gtk.Label(label=key)
                    field_label.set_xalign(0)
                    row.pack_start(field_label, True, True, 0)
                    choice = Gtk.ComboBoxText()
                    choice.append("plan", "A — Plan")
                    choice.append("repository", "B — OR")
                    choice.set_active(0)
                    row.pack_start(choice, False, False, 0)
                    field_choices[key] = choice
                    field_box.pack_start(row, False, False, 0)
                dialog.show_all()

            objects.connect("changed", refresh_fields)
            mode.connect("changed", refresh_fields)
            refresh_fields()
            response = dialog.run()
            name = objects.get_active_id()
            selected = mode.get_active_id()
            fields = {key: widget.get_active_id() for key, widget in field_choices.items()}
            dialog.destroy()
            if response != Gtk.ResponseType.OK or not name:
                return
            updated_plan, updated_repository = reconcile_object(
                self.plan, repository, name, selection=selected, fields=fields
            )
            persist_migration(self.path, destination, updated_plan, updated_repository)
            self.plan = updated_plan
            self.repository = updated_repository
            self._assigned_repository_token = None
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
            self.refresh_all()
            self.mark_dirty(False)
            self.set_status("Migrated %s to %s" % (name, destination.name))
        except Exception as exc:
            return self.error("Migrate Plan Objects", "%s: %s" % (type(exc).__name__, exc))


    def create_object_repository(self):
        """Explicitly create and assign a companion repository."""
        dialog = Gtk.FileChooserDialog(
            title="Create Object Repository", transient_for=self.window,
            action=Gtk.FileChooserAction.SAVE,
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL,
                           "Create", Gtk.ResponseType.OK)
        dialog.set_current_folder(str(self.path.parent))
        dialog.set_current_name(self.path.stem + REPOSITORY_SUFFIX)
        dialog.set_do_overwrite_confirmation(True)
        response = dialog.run()
        filename = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not filename:
            return False
        try:
            selected = Path(filename).resolve()
            if selected.suffix != REPOSITORY_SUFFIX:
                selected = selected.with_suffix(REPOSITORY_SUFFIX)
            if selected.exists():
                raise ValueError("repository already exists; use Assign Repository instead")
            ComponentRepository({}).save(selected)
            self.plan = assign_repositories(self.plan, self.path, (
                RepositoryAssociation(selected, RepositoryScope.LOCAL),
            ))
            self.assigned_repository_path = selected
            self._assigned_repository_token = None
            self.repository = ComponentRepository.load((selected,))
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
            self.mark_dirty()
            self.refresh_all()
            self.set_status("Created Object Repository: %s" % selected.name)
            return True
        except Exception as exc:
            self.error("Create Object Repository", "%s: %s" % (type(exc).__name__, exc))
            return False

    def ensure_repository_interactive(self):
        """Prompt before saving or recording when no OR is assigned."""
        path = assigned_repository_path(self.plan, self.path)
        if path is not None and path.is_file():
            return True
        dialog = Gtk.MessageDialog(
            transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.NONE,
            text="Object Repository Required",
        )
        dialog.format_secondary_text(
            "Select an existing Object Repository or create a new one. "
            "The test plan will reference repository-owned objects."
        )
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Select Existing…", 1)
        dialog.add_button("Create New…", 2)
        response = dialog.run()
        dialog.destroy()
        if response == 1:
            self.assign_object_repository()
        elif response == 2:
            self.create_object_repository()
        path = assigned_repository_path(self.plan, self.path)
        return path is not None and path.is_file()

    def assign_object_repository(self):
        selected = self.choose_file(title="Assign Object Repository", suffix=REPOSITORY_SUFFIX)
        if selected is None:
            return
        try:
            selected = selected.resolve()
            if not selected.is_file():
                raise ValueError("object repository does not exist")
            assigned = ComponentRepository.load((selected,))
            self.plan = assign_repositories(self.plan, self.path, (
                RepositoryAssociation(selected, RepositoryScope.LOCAL),
            ))
            self.assigned_repository_path = selected
            self._assigned_repository_token = None
            self.repository = assigned
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
            self.plan = assign_repositories(self.plan, self.path, (
                RepositoryAssociation(selected, RepositoryScope.LOCAL),
            ))
            self.assigned_repository_path = selected
            self._assigned_repository_token = None
            self.repository = central
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
        if not self.ensure_repository_interactive():
            return
        try:
            portable = self._save_portable_plan()
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
        self._refresh_assigned_repository()
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
                inputs_box.show_all()
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
            self.plan = insert_calls(self.plan, (call,), after_node_id=self.selected(self.flow_tree, 1))
        except Exception as exc:
            dialog.destroy()
            return self.error("Object Action", "%s: %s" % (type(exc).__name__, exc))
        dialog.destroy()
        self.mark_dirty()
        self.refresh_all()
        self.set_status("Added %s on %s" % (definition.name, component_id))
