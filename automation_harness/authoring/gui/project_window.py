from __future__ import annotations

import json
import threading
import webbrowser
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk

from automation_harness.authoring.gui.common import ArtifactWindow
from automation_harness.authoring.gui.result_report_viewer import ResultReportViewer
from automation_harness.reporting.run_reports import load_run_report
from automation_harness.authoring.plan_repository import assigned_repository_path
from automation_harness.authoring.preferences_runtime import (
    AuthoringPreferences, _restore_harness_windows, _suspend_harness_windows,
)
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.authoring.project_runs import (
    execute_project_batch, load_batches,
)
from automation_harness.authoring.step_registry import AuthoringStepRegistry, create_step_registry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_plan import load_plan, repository_from_plan, save_plan
from automation_harness.formats import PLAN_SUFFIX, REPOSITORY_SUFFIX, STEP_REGISTRY_SUFFIX
from automation_harness.models.plan import TestPlan


class ProjectWindow(ArtifactWindow):
    title_prefix = "Automation Harness Project"

    def __init__(self, path, *, opener=None):
        super().__init__(path, project_context=path, opener=opener)
        self.project = AuthoringProject.load(self.path)
        self.button("Save Project", self.save)
        self.button("Add Existing", self.add_existing)
        self.button("+ New Artifact", self.new_artifact)
        self.run_selected_button = self.button("Run Selected", self.run_selected)
        self.run_all_button = self.button("Run All", self.run_all)
        self.button("Refresh Runs", self.refresh_runs)
        self._run_active = False
        self._batches = ()
        self._shown_batch = None

        notebook = Gtk.Notebook()
        self.project_notebook = notebook
        self.root.pack_start(notebook, True, True, 0)
        artifacts_page = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        notebook.append_page(artifacts_page, Gtk.Label(label="Artifacts"))
        self.tree, self.store = self.list_tree((("Type", 170), ("Artifact", 300), ("Path", 520)))
        self.tree.get_selection().set_mode(Gtk.SelectionMode.MULTIPLE)
        self.tree.connect("row-activated", lambda *_args: self.open_selected())
        self.tree.connect("button-press-event", self._tree_button_press)
        self.tree.connect("key-press-event", self._tree_key_press)
        artifacts_page.pack1(self.scrolled(self.tree), resize=True, shrink=False)
        self.detail = Gtk.TextView(); self.detail.set_editable(False); self.detail.set_monospace(True)
        artifacts_page.pack2(self.scrolled(self.detail), resize=True, shrink=False)
        artifacts_page.set_position(720)
        self.tree.get_selection().connect("changed", lambda *_args: self.refresh_detail())
        self._build_runs_page(notebook)
        self.refresh()
        self.refresh_runs()

    def _build_runs_page(self, notebook):
        """Batch history, cross-plan failure summary and reusable single-run viewer."""
        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        notebook.append_page(outer, Gtk.Label(label="Test Runs"))

        history = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        history.set_border_width(8)
        history.pack_start(Gtk.Label(label="BATCH HISTORY", xalign=0), False, False, 0)
        self.batch_tree, self.batch_store = self.list_tree((
            ("Started", 180), ("Status", 85), ("Plans", 55)))
        self.batch_tree.get_selection().connect("changed", lambda *_args: self.show_batch())
        history.pack_start(self.scrolled(self.batch_tree), True, True, 0)

        plans_title = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        plans_title.pack_start(Gtk.Label(label="REPORTS IN BATCH", xalign=0), True, True, 0)
        self.plan_status_filter = Gtk.ComboBoxText()
        for value in ("All plans", "Failed only", "Passed only"):
            self.plan_status_filter.append_text(value)
        self.plan_status_filter.set_active(0)
        self.plan_status_filter.connect("changed", lambda *_args: self._populate_batch_plans())
        plans_title.pack_start(self.plan_status_filter, False, False, 0)
        history.pack_start(plans_title, False, False, 0)
        self.plan_run_tree, self.plan_run_store = self.list_tree((
            ("Plan", 210), ("Status", 75), ("Passed", 60), ("Failed", 60)))
        self.plan_run_tree.get_selection().connect("changed", lambda *_args: self.show_plan_run())
        history.pack_start(self.scrolled(self.plan_run_tree), True, True, 0)
        outer.pack1(history, resize=False, shrink=False)

        self.report_notebook = Gtk.Notebook()
        outer.pack2(self.report_notebook, resize=True, shrink=False)
        overview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        overview.set_border_width(8)
        self.batch_heading = Gtk.Label(label="Select a batch", xalign=0)
        self.batch_heading.set_selectable(True)
        overview.pack_start(self.batch_heading, False, False, 0)
        self.batch_summary = Gtk.Label(label="Select a historical batch to see aggregated results.", xalign=0)
        self.batch_summary.set_line_wrap(True)
        overview.pack_start(self.batch_summary, False, False, 0)
        overview.pack_start(Gtk.Label(label="FAILURES ACROSS REPORTS", xalign=0), False, False, 0)
        self.batch_failure_tree, self.batch_failure_store = self.list_tree((
            ("Plan", 170), ("Step", 220), ("Failure / Diagnostic", 400)))
        self.batch_failure_tree.connect("row-activated", lambda *_args: self._open_batch_failure())
        self.batch_failure_tree.get_selection().connect("changed", lambda *_args: self._open_batch_failure())
        overview.pack_start(self.scrolled(self.batch_failure_tree), True, True, 0)
        self.report_notebook.append_page(overview, Gtk.Label(label="Batch Overview"))
        self.report_viewer = ResultReportViewer(parent_window=self.window)
        self.report_notebook.append_page(self.report_viewer, Gtk.Label(label="Run Report"))
        outer.set_position(385)
        self._visible_plan_records = []
        self._batch_failure_refs = []

    def _populate_batch_plans(self):
        self.plan_run_store.clear()
        self._visible_plan_records = []
        if self._shown_batch is None:
            return
        chosen = self.plan_status_filter.get_active_text() or "All plans"
        for index, record in enumerate(self._shown_batch.plans):
            if chosen == "Failed only" and record.status == "passed":
                continue
            if chosen == "Passed only" and record.status != "passed":
                continue
            self._visible_plan_records.append(index)
            self.plan_run_store.append((
                record.plan_name, record.status.upper(), str(record.passed), str(record.failed),
            ))

    def _populate_batch_overview(self):
        self.batch_failure_store.clear()
        self._batch_failure_refs = []
        batch = self._shown_batch
        if batch is None:
            self.batch_heading.set_text("Select a batch")
            self.batch_summary.set_text("Select a historical batch to see aggregated results.")
            return

        plan_count = len(batch.plans)
        step_passed = sum(item.passed for item in batch.plans)
        step_failed = sum(item.failed for item in batch.plans)
        self.batch_heading.set_text("Batch %s    •    %s" % (batch.batch_id, batch.status.upper()))
        self.batch_summary.set_text(
            "%d plans: %d passed, %d failed or incomplete\n"
            "%d passed steps    •    %d failed steps\n"
            "Started: %s    Finished: %s\n"
            "Select a plan on the left, or a failure below, to drill into its report." %
            (plan_count, batch.passed, batch.failed, step_passed, step_failed,
             batch.started_at, batch.finished_at or "In progress")
        )
        for plan_index, record in enumerate(batch.plans):
            artifact_dir = Path(record.artifact_dir) if record.artifact_dir else None
            report = load_run_report(artifact_dir, fallback_record=record)
            if report is None:
                continue
            failures = [step for step in report.steps if step.status == "FAILED"]
            for step in failures:
                self._batch_failure_refs.append((plan_index, step.node_id))
                self.batch_failure_store.append((
                    record.plan_name, step.name, step.error or "Step failed",
                ))
            if record.status != "passed" and not failures:
                self._batch_failure_refs.append((plan_index, None))
                self.batch_failure_store.append((
                    record.plan_name, "Run diagnostic",
                    record.error or "; ".join(report.diagnostics) or "No step-level evidence available",
                ))

    def _open_batch_failure(self):
        index = self._tree_index(self.batch_failure_tree)
        if index is None or index >= len(self._batch_failure_refs):
            return
        plan_index, node_id = self._batch_failure_refs[index]
        if plan_index not in self._visible_plan_records:
            self.plan_status_filter.set_active(0)
        try:
            visible_index = self._visible_plan_records.index(plan_index)
        except ValueError:
            return
        self.plan_run_tree.get_selection().select_path(visible_index)
        self.report_notebook.set_current_page(1)
        if node_id:
            self.report_viewer.select_step(node_id)

    def refresh(self):
        self.store.clear()
        groups = (
            ("Test Plan", self.project.test_plans),
            ("Step Registry", self.project.step_registries),
            ("Object Repository", self.project.object_repositories),
        )
        for label, paths in groups:
            for path in paths:
                self.store.append((label, path.stem, str(path)))
        self.set_status(
            "%d plans • %d registries • %d repositories" %
            (len(self.project.test_plans), len(self.project.step_registries), len(self.project.object_repositories))
        )
        self.refresh_detail()

    def _selected_artifacts(self):
        """Return selected artifact rows without using GTK's single-selection API."""
        model, paths = self.tree.get_selection().get_selected_rows()
        return tuple((model[path][0], model[path][2]) for path in paths)

    def _selected_artifact(self):
        selected = self._selected_artifacts()
        return selected[0] if len(selected) == 1 else None

    def _selected_plan_paths(self):
        selected = {
            Path(path_text).resolve()
            for label, path_text in self._selected_artifacts()
            if label == "Test Plan"
        }
        return tuple(path for path in self.project.test_plans if path.resolve() in selected)

    def refresh_detail(self):
        selected_artifacts = self._selected_artifacts()
        if len(selected_artifacts) > 1:
            plans = self._selected_plan_paths()
            self.detail.get_buffer().set_text(
                "%d artifacts selected%s" % (len(selected_artifacts), "\n\n%d Test Plans ready to run." % len(plans) if plans else "")
            )
            return
        selected = self._selected_artifact()
        if selected is None:
            path_text = None
            label = None
        else:
            label, path_text = selected
        if not path_text:
            text = (
                "%s\n\nTest Plans: %d\nStep Registries: %d\nObject Repositories: %d" %
                (self.project.name, len(self.project.test_plans), len(self.project.step_registries), len(self.project.object_repositories))
            )
            self.detail.get_buffer().set_text(text)
            return

        artifact = Path(path_text)
        if not artifact.is_file():
            self.detail.get_buffer().set_text("%s\n\nThe artifact file is missing." % artifact.stem)
            return

        lines = [label.upper(), "", artifact.stem]
        try:
            if label == "Object Repository":
                repository = ComponentRepository.load((artifact,))
                type_counts = {}
                for definition in repository.components.values():
                    object_type = definition.object_type.value
                    type_counts[object_type] = type_counts.get(object_type, 0) + 1
                lines.append("%d objects" % len(repository.components))
                if type_counts:
                    lines.append("Types: %s" % ", ".join("%s (%d)" % item for item in sorted(type_counts.items())))

            elif label == "Test Plan":
                plan = load_plan(artifact)
                assigned = assigned_repository_path(plan, artifact)
                lines.extend((
                    "%d steps · %d variables" % (len(plan.steps), len(plan.variables)),
                    "Object repository: %s" % (assigned.stem if assigned is not None else "embedded"),
                ))

            elif label == "Step Registry":
                registry = AuthoringStepRegistry.load(artifact)
                lines.extend((
                    "%d reusable steps" % len(registry.steps),
                    "Object repository: %s" % Path(registry.repository).stem,
                ))
        except Exception as exc:
            lines.extend(("", "Unable to inspect artifact:", "%s: %s" % (type(exc).__name__, exc)))

        self.detail.get_buffer().set_text("\n".join(lines))

    def _runs_dir(self):
        runs_dir = AuthoringPreferences.load().resolved_runs_dir(self.project)
        runs_dir.mkdir(parents=True, exist_ok=True)
        return runs_dir

    def refresh_runs(self, *, preferred_batch_id=None):
        if self._run_active:
            return
        previous_batch = preferred_batch_id or getattr(
            getattr(self, "_shown_batch", None), "batch_id", None,
        )
        try:
            self._batches = load_batches(self._runs_dir(), self.path)
        except Exception as exc:
            self._batches = ()
            self.set_status("Unable to load run history: %s" % exc)
        self._shown_batch = None
        self.batch_store.clear()
        for batch in self._batches:
            self.batch_store.append((batch.started_at, batch.status.upper(), str(len(batch.plans))))
        self._populate_batch_plans()
        self._populate_batch_overview()
        self.report_viewer.set_report(None)
        self.report_notebook.set_current_page(0)
        if self._batches:
            index = next((i for i, batch in enumerate(self._batches)
                          if batch.batch_id == previous_batch), 0)
            self.batch_tree.get_selection().select_path(index)

    def show_batch(self):
        index = self._tree_index(self.batch_tree)
        self._shown_batch = self._batches[index] if index is not None and index < len(self._batches) else None
        self._populate_batch_plans()
        self._populate_batch_overview()
        self.report_viewer.set_report(None)
        self.report_notebook.set_current_page(0)

    @staticmethod
    def _tree_index(tree):
        model, iterator = tree.get_selection().get_selected()
        if iterator is None:
            return None
        return model.get_path(iterator).get_indices()[0]

    def _selected_plan_run(self):
        index = self._tree_index(self.plan_run_tree)
        if (self._shown_batch is None or index is None or
                index >= len(self._visible_plan_records)):
            return None
        return self._shown_batch.plans[self._visible_plan_records[index]]

    def show_plan_run(self):
        record = self._selected_plan_run()
        if record is None:
            self.report_viewer.set_report(None)
            return
        artifact_dir = Path(record.artifact_dir) if record.artifact_dir else None
        report = load_run_report(artifact_dir, fallback_record=record)
        self.report_viewer.set_report(report)
        self.report_notebook.set_current_page(1)

    def run_selected(self):
        self._run_plans(self._selected_plan_paths())

    def run_all(self):
        self._run_plans(self.project.test_plans)

    def _run_plans(self, plan_paths):
        if self._run_active:
            return
        if not plan_paths:
            return self.info("Run Test Plans", "Select one or more Test Plans first.")
        try:
            runs_dir = self._runs_dir()
        except Exception as exc:
            return self.error("Test runs folder", "%s: %s" % (type(exc).__name__, exc))
        self._run_active = True
        self.run_selected_button.set_sensitive(False); self.run_all_button.set_sensitive(False)
        self.set_status("Preparing desktop for %d test plan(s)…" % len(plan_paths))
        _suspend_harness_windows(self, Gtk, Gdk)

        def worker():
            def progress(phase, index, total, record):
                GLib.idle_add(self._show_batch_progress, phase, index, total, record)
            try:
                batch = execute_project_batch(self.project, self.path, plan_paths, runs_dir=runs_dir, progress=progress)
                GLib.idle_add(self._batch_finished, batch, None)
            except Exception as exc:
                GLib.idle_add(self._batch_finished, None, exc)

        GLib.timeout_add(350, lambda: (threading.Thread(target=worker, name="automation-project-batch-run", daemon=True).start(), False)[1])

    def _show_batch_progress(self, phase, index, total, record):
        if phase == "started":
            self.set_status("Running %d/%d: %s" % (index, total, record.plan_name))
        else:
            self.set_status("Completed %d/%d: %s" % (index, total, record.plan_name))
        return False

    def _batch_finished(self, batch, error):
        self._run_active = False
        self.run_selected_button.set_sensitive(True); self.run_all_button.set_sensitive(True)
        self.refresh_runs(preferred_batch_id=batch.batch_id if batch is not None else None)
        # Never leave the user on the Artifacts tab after running a batch.
        self.project_notebook.set_current_page(1)
        if batch is not None and len(batch.plans) == 1 and self._visible_plan_records:
            self.plan_run_tree.get_selection().select_path(0)
        _restore_harness_windows(self)
        if error is not None:
            self.set_status("Batch run failed")
            self.error("Run Test Plans", "%s: %s" % (type(error).__name__, error))
        elif batch is not None:
            self.set_status("Batch %s: %d passed, %d failed" % (batch.status, batch.passed, batch.failed))
        return False

    def _tree_button_press(self, _tree, event):
        if event.button != 3:
            return False
        path_info = self.tree.get_path_at_pos(int(event.x), int(event.y))
        if path_info is None:
            return False
        path, _column, _cell_x, _cell_y = path_info
        selection = self.tree.get_selection()
        if not selection.path_is_selected(path):
            selection.unselect_all()
            selection.select_path(path)
        menu = Gtk.Menu()
        open_item = Gtk.MenuItem(label="Open")
        open_item.connect("activate", lambda *_args: self.open_selected())
        menu.append(open_item)
        remove_item = Gtk.MenuItem(label="Remove from Project")
        remove_item.connect("activate", lambda *_args: self.remove_selected())
        menu.append(remove_item)
        menu.show_all()
        menu.popup_at_pointer(None)
        return True

    def _tree_key_press(self, _tree, event):
        if event.keyval == Gdk.KEY_Delete:
            self.remove_selected()
            return True
        return False

    def save(self):
        save_authoring_project(self.path, self.project)
        self.mark_dirty(False); self.set_status("Saved project")

    def open_selected(self):
        selected = self._selected_artifact()
        if selected is None:
            if self._selected_artifacts():
                return self.info("Project", "Select exactly one artifact to open.")
            return
        _label, path_text = selected
        return self.open_artifact(Path(path_text), project_context=self.path)

    def remove_selected(self):
        selected = self._selected_artifact()
        if selected is None:
            message = "Select exactly one artifact to remove." if self._selected_artifacts() else "Select an artifact first."
            return self.info("Project", message)
        label, path_text = selected
        path = Path(path_text)
        if not self.confirm("Remove from Project", "Remove %s from Project membership? The file will not be deleted." % path.name):
            return
        if label == "Test Plan": self.project = self.project.without_test_plan(path)
        elif label == "Step Registry": self.project = self.project.without_step_registry(path)
        else: self.project = self.project.without_object_repository(path)
        self.mark_dirty(); self.refresh()

    def add_existing(self):
        path = self.choose_file(title="Add existing artifact")
        if path is None:
            return
        from automation_harness.authoring.gui.router import ArtifactType, detect_artifact
        try:
            kind = detect_artifact(path)
            if kind is ArtifactType.TEST_PLAN: self.project = self.project.with_test_plan(path)
            elif kind is ArtifactType.STEP_REGISTRY:
                registry = AuthoringStepRegistry.load(path)
                self.project = self.project.with_step_registry(path).with_object_repository(registry.repository)
            elif kind is ArtifactType.OBJECT_REPOSITORY: self.project = self.project.with_object_repository(path)
            else: return self.error("Project", "A Project cannot be added as a child artifact.")
        except Exception as exc:
            return self.error("Project", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(); self.refresh()

    def new_artifact(self):
        dialog = Gtk.Dialog(title="New Project Artifact", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Create", Gtk.ResponseType.OK)
        combo = Gtk.ComboBoxText(); combo.append("plan", "Test Plan"); combo.append("registry", "Step Registry"); combo.append("repository", "Object Repository"); combo.set_active(0)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10); box.pack_start(combo, False, False, 0)
        dialog.show_all(); response = dialog.run(); kind = combo.get_active_id(); dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        if kind == "plan": suffix = PLAN_SUFFIX; title = "Create Test Plan"
        elif kind == "registry": suffix = STEP_REGISTRY_SUFFIX; title = "Create Step Registry"
        else: suffix = REPOSITORY_SUFFIX; title = "Create Object Repository"
        path = self.choose_file(title=title, save=True, suffix=suffix)
        if path is None:
            return
        name = self.ask_text(title, "Name:", path.stem)
        if not name:
            return
        try:
            if kind == "plan":
                save_plan(TestPlan(name=name), path); self.project = self.project.with_test_plan(path)
            elif kind == "registry":
                registry = create_step_registry(path, name); self.project = self.project.with_step_registry(path).with_object_repository(registry.repository)
            else:
                ComponentRepository({}).save(path); self.project = self.project.with_object_repository(path)
        except Exception as exc:
            return self.error(title, "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(); self.save(); self.refresh(); self.open_artifact(path, project_context=self.path)
