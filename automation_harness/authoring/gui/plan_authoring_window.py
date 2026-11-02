from __future__ import annotations

import json
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from automation_harness.authoring.action_catalog import actions_for
from automation_harness.authoring.plan_open import (\n    inspect_plan_open, PlanOpenState, qualify_migration_repository_selection,\n)
from automation_harness.authoring.diagnostic_events import emit, artifact_fingerprint
from automation_harness.authoring.gui.action_widgets import (
    create_action_input_widget,
    interaction_actions,
    read_action_input,
)
from automation_harness.authoring.gui.plan_window import TestPlanWindow, _next_node_id
from automation_harness.authoring.gui.embedded_repository_dialog import review_embedded_repository
from automation_harness.authoring.plan_repository import (
    assign_repositories,
    assigned_repository_path,
    load_repository_set,
)
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.authoring.step_order import insert_calls
from automation_harness.core.component_repository import ComponentRepository, repository_fingerprint
from automation_harness.core.repository_migration import (
    prepare_embedded_migration, publish_embedded_migration,
)
from automation_harness.core.repository_scope import RepositoryAssociation, RepositoryScope
from automation_harness.core.reusable_step_snapshot import snapshot_reusable_dependencies
from automation_harness.core.test_plan import embed_plan_repository, repository_from_plan, save_plan
from automation_harness.formats import REPOSITORY_SUFFIX
from automation_harness.runner.plan_resources import resolve_plan_resources


class TestPlanAuthoringWindow(TestPlanWindow):
    """Concrete test composition workflow layered on the Test Plan document window."""

    def __init__(self, *args, **kwargs):
        path = args[0] if args else kwargs["path"]
        inspected = inspect_plan_open(path)
        self._legacy_pending = inspected.state is PlanOpenState.MIGRATION_REQUIRED
        kwargs["preloaded_plan"] = inspected.plan
        kwargs["defer_repository"] = self._legacy_pending
        super().__init__(*args, **kwargs)
        self._loaded_plan_fingerprint = repository_fingerprint(self.path)
        self.assigned_repository_path = assigned_repository_path(self.plan, self.path)
        if (not self._legacy_pending and self.assigned_repository_path is not None
                and self.assigned_repository_path.exists()):
            self.repository = resolve_plan_resources(self.plan, self.path, project=self.project).repository
        self.objects_button = self.button("Objects", self.show_objects_menu)
        self.window.show_all()
        if self._legacy_pending:
            self.set_status("Legacy objects detected; use Objects > Migrate Embedded Objects before editing")

    def show_objects_menu(self):
        self.refresh_objects()
        menu = Gtk.Menu()

        def add_item(label, callback, sensitive=True):
            item = Gtk.MenuItem(label=label)
            item.set_sensitive(sensitive)
            item.connect("activate", lambda *_args: callback())
            menu.append(item)

        add_item("Add Object Action", self.add_object_action, not self._legacy_pending)
        menu.append(Gtk.SeparatorMenuItem())
        add_item("Assign Repository…", self.assign_object_repository, not self._legacy_pending)
        add_item("Inspect Embedded Repository…", self.inspect_embedded_repository)
        add_item("Migrate Embedded Objects…", self.merge_assign_central_repository,
                 bool(self.plan.objects))
        add_item("Export Portable Snapshot…", self.export_portable_snapshot,
                 self.plan.execution_mode == "canonical" and not self._legacy_pending)
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
            self.plan = assign_repositories(self.plan, self.path, (
                RepositoryAssociation(selected, RepositoryScope.LOCAL),
            ))
            self.assigned_repository_path = selected
            self._assigned_repository_token = None
            if self.project_context:
                project = AuthoringProject.load(self.project_context).with_object_repository(selected)
                save_authoring_project(self.project_context, project)
                self.project = project
            self.repository = resolve_plan_resources(self.plan, self.path, project=self.project).repository
        except Exception as exc:
            return self.error("Assign Object Repository", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty()
        self.refresh_all()
        self.set_status("Assigned object repository: %s" % selected.name)

    def inspect_embedded_repository(self):
        """Inspect the plan snapshot independently of the effective repository."""
        return review_embedded_repository(self.window, self.plan)

    def merge_assign_central_repository(self):
        """Promote embedded definitions into the chosen repository by UUID."""
        if self.dirty:
            return self.info("Migrate Embedded Objects",
                "Save your current Test Plan changes before migration.")
        if not self.plan.objects:
            return self.info("Migrate Embedded Objects",
                "This plan has no embedded objects to migrate.")
        selected = self.choose_file(title="Choose Canonical Object Repository", suffix=REPOSITORY_SUFFIX)
        if selected is None:
            return
        selected = selected.resolve()
        if not selected.is_file():
            return self.error("Migrate Embedded Objects", "The destination repository does not exist.")
        current_path = assigned_repository_path(self.plan, self.path)
        try:
            recovering_missing = qualify_migration_repository_selection(
                self.plan, self.path, selected,
            )
        except ValueError as exc:
            return self.error("Migrate Embedded Objects", str(exc))
        if recovering_missing:
            # A missing repository is not a license to arbitrarily reassign
            # object identity. Require explicit recovery consent before the
            # existing conflict-review and atomic publication gates.
            if not self.confirm("Recover Missing Repository Assignment",
                    "The plan's previous repository is missing:\\n%s\\n\\n"
                    "Use %s as a replacement only through reviewed migration? "
                    "Cancellation leaves all files and assignments unchanged."
                    % (current_path, selected)):
                emit("migration_cancelled", state="missing_repository_recovery")
                return
        try:
            if repository_fingerprint(self.path) != self._loaded_plan_fingerprint:
                raise ValueError("the plan changed on disk; reopen it before migration")
            repository_baseline = repository_fingerprint(selected)
            canonical = ComponentRepository.load((selected,))
            if repository_fingerprint(selected) != repository_baseline:
                raise ValueError("repository changed during migration inspection")
            project_baseline = None
            projected_project = None
            if self.project_context:
                project_baseline = repository_fingerprint(self.project_context)
                projected_project = AuthoringProject.load(self.project_context)
                if repository_fingerprint(self.project_context) != project_baseline:
                    raise ValueError("Project changed during migration inspection")
                projected_project = projected_project.with_object_repository(selected).with_test_plan(self.path)
        except Exception as exc:
            return self.error("Migrate Embedded Objects", "%s: %s" % (type(exc).__name__, exc))
        choices = review_embedded_repository(self.window, self.plan, canonical, migrate=True)
        if choices is None:
            emit("migration_cancelled")
            return
        resolutions, renames, distinct = choices
        try:
            emit("migration_prepare_started", plan_fingerprint=self._loaded_plan_fingerprint.__dict__
                 if hasattr(self._loaded_plan_fingerprint, "__dict__") else artifact_fingerprint(self.path),
                 repository_fingerprint=artifact_fingerprint(selected))
            proposal = prepare_embedded_migration(
                self.plan, self.path, canonical, selected,
                resolutions=resolutions, renames=renames,
                approved_distinct=distinct,
            )
        except Exception as exc:
            emit("migration_prepare_failed", error_type=type(exc).__name__)
            return self.error("Migration Preview", "%s: %s" % (type(exc).__name__, exc))
        if not self.confirm("Publish Canonical Migration",
                "Migrate %d embedded object(s) into %s?\\n"
                "%d added, %d reused. The plan will reference canonical UUIDs "
                "and will no longer contain embedded object definitions. "
                "Portable execution will require an explicit export/snapshot.\\n\\n"
                "This updates both the Test Plan and its Object Repository."
                % (len(self.plan.objects), selected.name,
                   len(proposal.added_ids), len(proposal.reused_ids))):
            return
        try:
            # Fail closed if the previous missing repository appeared while
            # the operator was reviewing collision decisions.
            qualify_migration_repository_selection(self.plan, self.path, selected)
            baselines = {
                selected: repository_baseline,
                self.path: self._loaded_plan_fingerprint,
            }
            if self.project_context:
                baselines[self.project_context] = project_baseline
            emit("migration_publish_started", plan_fingerprint=artifact_fingerprint(self.path),
                 repository_fingerprint=artifact_fingerprint(selected))
            publish_embedded_migration(
                proposal, repository_path=selected, plan_path=self.path,
                expected_fingerprints=baselines,
                project_path=self.project_context, project=projected_project,
            )
            emit("migration_publish_completed", imported_count=len(proposal.added_ids),
                 reused_count=len(proposal.reused_ids), plan_fingerprint=artifact_fingerprint(self.path),
                 repository_fingerprint=artifact_fingerprint(selected))
            self.plan = proposal.plan
            self._legacy_pending = False
            self._defer_repository_composition = False
            self._loaded_plan_fingerprint = repository_fingerprint(self.path)
            self.assigned_repository_path = selected
            self._assigned_repository_token = None
            if self.project_context:
                self.project = projected_project
            self.repository = resolve_plan_resources(self.plan, self.path, project=self.project).repository
        except Exception as exc:
            emit("migration_publish_failed", error_type=type(exc).__name__)
            return self.error("Migrate Embedded Objects", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(False)
        self.refresh_all()
        self.set_status("Migrated to canonical repository %s: %d objects imported"
                        % (selected.name, len(proposal.added_ids)))

    def export_portable_snapshot(self):
        """Create a separate self-contained plan without changing ownership."""
        if self.plan.execution_mode != "canonical":
            return self.info("Export Portable Snapshot",
                "This command exports canonical-only plans. Other plans already contain snapshots.")
        if self.dirty:
            return self.info("Export Portable Snapshot",
                "Save Test Plan changes before creating a portable copy.")
        selected = self.choose_file(title="Export Portable Plan Copy", save=True, suffix=".ahplan")
        if selected is None:
            return
        selected = selected.resolve()
        if selected == self.path:
            return self.error("Export Portable Snapshot",
                "Choose a different output path; the canonical plan will not be overwritten.")
        try:
            from automation_harness.core.reusable_step_expansion import expand_reusable_steps
            portable = replace(self.plan, execution_mode="portable")
            if self.reusable:
                portable = snapshot_reusable_dependencies(portable, self.reusable, self.repository)
            expanded = expand_reusable_steps(portable, self.reusable) if self.reusable else portable
            embedded = embed_plan_repository(expanded, self.repository)
            portable = replace(portable, objects=embedded.objects,
                               step_definitions=embedded.step_definitions)
            save_plan(portable, selected)
        except Exception as exc:
            return self.error("Export Portable Snapshot", "%s: %s" % (type(exc).__name__, exc))
        self.set_status("Exported portable snapshot: %s" % selected.name)

    def open_assigned_repository(self):
        path = assigned_repository_path(self.plan, self.path)
        if path is None:
            return self.info("Object Repository", "This Test Plan does not currently have an assigned Object Repository.")
        return self.open_artifact(path, project_context=self.project_context)

    def save(self):
        """Persist direct objects first, then add transitive Registry dependencies."""
        if self._legacy_pending:
            return self.error("Save Test Plan",
                "Migrate embedded legacy objects before saving this plan.")
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
        self._loaded_plan_fingerprint = repository_fingerprint(self.path)
        self.mark_dirty(False)
        self.set_status("Saved canonical test plan" if self.plan.execution_mode == "canonical"
                        else "Saved portable test plan")

    def add_object_action(self, component_id=None, action_id=None):
        if self._legacy_pending:
            return self.error("Object Action",
                "Migrate embedded legacy objects before editing this plan.")
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
            canonical_object_id = self.repository.get(component_id).object_id
            call = replace(definition.to_step_call(
                _next_node_id(self.plan.steps), canonical_object_id, values,
            ), group=group)
            self.plan = insert_calls(self.plan, (call,), after_node_id=self.selected(self.flow_tree, 1))
        except Exception as exc:
            dialog.destroy()
            return self.error("Object Action", "%s: %s" % (type(exc).__name__, exc))
        dialog.destroy()
        self.mark_dirty()
        self.refresh_all()
        self.set_status("Added %s on %s" % (definition.name, component_id))
