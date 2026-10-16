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
from automation_harness.authoring.diagnostic_events import emit, artifact_fingerprint
from automation_harness.authoring.plan_repository import (
    assign_repositories,
    assigned_repository_path,
    load_repository_set,
    merge_repository_or,
)
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_migration import reconcile_object, persist_migration, stage_legacy_migration, detect_legacy_objects, stage_reconciled_migration
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
            # The editor remains available for repository recovery, but cannot
            # save or run while embedded definitions remain unresolved.
            self.set_status("Legacy migration pending — use Objects to resolve the repository")

    def _detect_and_migrate_legacy_on_open(self):
        """Detect immediately after loading, before normal editing begins."""
        if not self.plan.objects:
            emit('migration_not_required', plan=str(self.path))
            return True
        original_plan = self.plan
        destination = assigned_repository_path(self.plan, self.path)
        if destination is None or not destination.is_file():
            # Legacy files may carry only a recording association or a moved OR.
            # Ask for a replacement without persisting it before migration.
            dialog = Gtk.MessageDialog(
                transient_for=self.window, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.NONE,
                text="Resolve Legacy Object Repository",
            )
            dialog.format_secondary_text(
                ("Assigned repository not found: %s. " % destination if destination else
                 "This plan has no authoring repository association. ") +
                "Select the existing repository used by this plan. "
                "Cancel preserves the original plan and repository."
            )
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button("Locate Repository…", Gtk.ResponseType.OK)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                emit("migration_cancelled", plan=str(self.path))
                return False
            selected = self.choose_file(title="Locate Existing Object Repository", suffix=REPOSITORY_SUFFIX)
            if selected is None:
                return False
            selected = selected.resolve()
            if not selected.is_file():
                self.error("Legacy Repository", "Selected repository does not exist: %s" % selected)
                return False
            try:
                ComponentRepository.load((selected,))
                self.plan = assign_repositories(self.plan, self.path, (
                    RepositoryAssociation(selected, RepositoryScope.LOCAL),
                ))
            except Exception as exc:
                self.error("Legacy Repository", "%s: %s" % (type(exc).__name__, exc))
                return False
        try:
            destination = assigned_repository_path(self.plan, self.path)
            repository = ComponentRepository.load((destination,))
            safe, conflicts = detect_legacy_objects(self.plan, repository)
            emit('migration_classified', plan=str(self.path), repository=str(destination), safe_count=len(safe), conflict_count=len(conflicts), repository_fingerprint=artifact_fingerprint(destination))
            if conflicts:
                self.error(
                    "Legacy Object Reconciliation Required",
                    "Legacy definitions conflict with the assigned repository. "
                    "Use Objects → Migrate Plan Objects to resolve them.\\n\\n"
                    + "\\n".join(conflicts),
                )
                completed = bool(self.migrate_plan_objects())
                if not completed:
                    self.plan = original_plan
                return completed
            if not self.confirm(
                "Migrate Legacy Objects",
                "Detected %d legacy object(s) in this plan. Move them into %s "
                "before editing? Cancel preserves the original files."
                % (len(safe), destination.name),
            ):
                self.plan = original_plan
                return False
            emit('migration_commit_started', plan=str(self.path), repository=str(destination), plan_before=artifact_fingerprint(self.path), repository_before=artifact_fingerprint(destination))
            migrated, updated_repository = stage_legacy_migration(self.plan, repository)
            persist_migration(self.path, destination, migrated, updated_repository)
            emit('migration_commit_completed', plan=str(self.path), repository=str(destination), plan_after=artifact_fingerprint(self.path), repository_after=artifact_fingerprint(destination))
            self.plan = migrated
            self._defer_repository_composition = False
            self.repository = updated_repository
            self._assigned_repository_token = None
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
            self.refresh_all()
            self.mark_dirty(False)
            self.set_status("Migrated %d legacy objects on open" % len(safe))
            return True
        except Exception as exc:
            emit("migration_failed", plan=str(self.path), error_type=type(exc).__name__, error=str(exc))
            self.plan = original_plan
            self.error("Legacy Migration", "%s: %s" % (type(exc).__name__, exc))
            return False

    def show_objects_menu(self):
        # Repository refresh can fail while legacy objects are being repaired;
        # the recovery menu must remain available regardless.
        try:
            self.refresh_objects()
        except Exception as exc:
            emit("objects_menu_refresh_failed", plan=str(self.path), error=str(exc))
            self.set_status("Object refresh failed: %s" % exc)
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
        self._objects_popup_menu = menu
        menu.show_all()
        menu.popup_at_widget(self.objects_button, Gdk.Gravity.SOUTH, Gdk.Gravity.NORTH, None)


    def migrate_plan_objects(self):
        """Collect all decisions, then commit the plan and repository once."""
        destination = assigned_repository_path(self.plan, self.path)
        if destination is None or not destination.is_file():
            return self.error("Migrate Plan Objects", "Assign an existing Object Repository first.")
        try:
            before_plan = artifact_fingerprint(self.path).get("sha256")
            before_repository = artifact_fingerprint(destination).get("sha256")
            repository = ComponentRepository.load((destination,))
            existing = repository.to_document()["components"]
            dialog = Gtk.Dialog(title="Reconcile Legacy Plan Objects", transient_for=self.window, modal=True)
            dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Apply All", Gtk.ResponseType.OK)
            area = dialog.get_content_area()
            area.set_spacing(8)
            area.set_border_width(10)
            area.pack_start(Gtk.Label(label="Resolve every object before applying. Cancel leaves both files unchanged."), False, False, 0)
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            scroll.set_min_content_height(350)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            scroll.add(content)
            area.pack_start(scroll, True, True, 0)
            widgets = {}
            for name, source in sorted(self.plan.objects.items()):
                frame = Gtk.Frame(label=name)
                section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
                section.set_border_width(6)
                frame.add(section)
                content.pack_start(frame, False, False, 0)
                targets = [key for key, obj in existing.items()
                           if key == name or obj.get("object_id") == source.get("object_id")]
                target_choice = Gtk.ComboBoxText()
                target_choice.append("", "New repository object")
                for key in targets:
                    target_choice.append(key, key)
                target_choice.set_active(1 if targets else 0)
                section.pack_start(target_choice, False, False, 0)
                mode = Gtk.ComboBoxText()
                for key, title in (("plan", "A - Plan"), ("repository", "B - Repository"),
                                   ("combine", "Combine - choose each differing field")):
                    mode.append(key, title)
                # Prefer existing repository definitions when resolving a collision.
                mode.set_active(1 if targets else 0)
                section.pack_start(mode, False, False, 0)
                identity = Gtk.CheckButton(label="Explicitly map legacy UUID to selected repository UUID")
                section.pack_start(identity, False, False, 0)
                fields = {}
                for target_name in targets:
                    other = existing[target_name]
                    for field in sorted(set(source) | set(other)):
                        if field in ("object_id", "owner_object_id") or source.get(field) == other.get(field):
                            continue
                        if field in fields:
                            continue
                        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                        row.pack_start(Gtk.Label(label=field), True, True, 0)
                        choice = Gtk.ComboBoxText()
                        choice.append("plan", "A - Plan")
                        choice.append("repository", "B - Repository")
                        choice.set_active(0)
                        row.pack_start(choice, False, False, 0)
                        section.pack_start(row, False, False, 0)
                        fields[field] = choice
                widgets[name] = (target_choice, mode, identity, fields)
            dialog.show_all()
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                dialog.destroy()
                emit("migration_cancelled", plan=str(self.path))
                return False
            decisions = {}
            for name, (target_choice, mode, identity, fields) in widgets.items():
                target_name = target_choice.get_active_id() or name
                target = existing.get(target_name)
                decisions[name] = {
                    "target_name": target_name,
                    "selection": mode.get_active_id(),
                    "fields": {key: widget.get_active_id() for key, widget in fields.items()},
                    "identity_mapping": target["object_id"] if target and identity.get_active() else None,
                }
            dialog.destroy()
            staged_plan, staged_repository = stage_reconciled_migration(self.plan, repository, decisions)
            emit("migration_commit_started", plan=str(self.path), repository=str(destination),
                 plan_before=artifact_fingerprint(self.path), repository_before=artifact_fingerprint(destination))
            persist_migration(self.path, destination, staged_plan, staged_repository,
                              expected_fingerprints=(before_plan, before_repository))
            emit("migration_commit_completed", plan=str(self.path), repository=str(destination),
                 plan_after=artifact_fingerprint(self.path), repository_after=artifact_fingerprint(destination))
            self.plan = staged_plan
            self._defer_repository_composition = False
            self.repository = staged_repository
            self._assigned_repository_token = None
            if self.registry_resources:
                self.repository = self.repository.overlay(self.registry_resources.repository)
            self.refresh_all()
            self.mark_dirty(False)
            self.set_status("Migrated %d legacy objects" % len(decisions))
            return True
        except Exception as exc:
            emit("migration_failed", plan=str(self.path), error_type=type(exc).__name__, error=str(exc))
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
            self.plan = replace(self.plan, steps=(*self.plan.steps, call))
        except Exception as exc:
            dialog.destroy()
            return self.error("Object Action", "%s: %s" % (type(exc).__name__, exc))
        dialog.destroy()
        self.mark_dirty()
        self.refresh_all()
        self.set_status("Added %s on %s" % (definition.name, component_id))
