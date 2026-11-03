"""Reusable GTK3 report inspector for plan and project execution windows."""
from __future__ import annotations

import webbrowser

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from automation_harness.reporting.run_reports import RunReport


class ResultReportViewer(Gtk.Box):
    """One report presentation, independent of how the run was selected."""

    def __init__(self, *, parent_window=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.set_border_width(8)
        self._parent_window = parent_window
        self._report = None
        self._visible_steps = []

        heading_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.pack_start(heading_row, False, False, 0)
        self.heading = Gtk.Label(label="Result report", xalign=0)
        self.heading.set_use_markup(True)
        heading_row.pack_start(self.heading, True, True, 0)
        self.html_button = Gtk.Button(label="Open HTML Report")
        self.html_button.connect("clicked", self._open_html)
        heading_row.pack_start(self.html_button, False, False, 0)
        self.folder_button = Gtk.Button(label="Run Folder")
        self.folder_button.connect("clicked", self._open_folder)
        heading_row.pack_start(self.folder_button, False, False, 0)

        self.summary = Gtk.Label(xalign=0)
        self.summary.set_line_wrap(True)
        self.summary.set_selectable(True)
        self.pack_start(self.summary, False, False, 0)

        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.pack_start(filters, False, False, 0)
        filters.pack_start(Gtk.Label(label="Execution timeline", xalign=0), False, False, 0)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Find step, action, or node ID")
        self.search.connect("changed", lambda *_args: self._populate())
        filters.pack_start(self.search, True, True, 0)
        self.step_filter = Gtk.ComboBoxText()
        for value in ("All steps", "Failed", "Passed", "Running", "Not run", "Skipped", "Cancelled"):
            self.step_filter.append_text(value)
        self.step_filter.set_active(0)
        self.step_filter.connect("changed", lambda *_args: self._populate())
        filters.pack_start(self.step_filter, False, False, 0)

        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.pack_start(panes, True, True, 0)
        self.step_store = Gtk.ListStore(str, str, str)
        self.step_tree = Gtk.TreeView(model=self.step_store)
        for column, width, index in (("#", 46, 0), ("Result", 100, 1), ("Step name", 290, 2)):
            renderer = Gtk.CellRendererText()
            view_column = Gtk.TreeViewColumn(column, renderer, text=index)
            view_column.set_resizable(True)
            view_column.set_min_width(width)
            self.step_tree.append_column(view_column)
        self.step_tree.get_selection().connect("changed", lambda *_args: self._show_step())
        left = Gtk.ScrolledWindow()
        left.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        left.add(self.step_tree)
        panes.pack1(left, resize=True, shrink=False)

        self.detail = Gtk.TextView()
        self.detail.set_editable(False)
        self.detail.set_cursor_visible(False)
        self.detail.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.detail.set_left_margin(12)
        self.detail.set_right_margin(12)
        self.detail.set_top_margin(10)
        right = Gtk.ScrolledWindow()
        right.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        right.add(self.detail)
        panes.pack2(right, resize=True, shrink=False)
        panes.set_position(430)

        self.set_report(None)

    @property
    def report(self):
        return self._report

    def set_report(self, report: RunReport | None):
        self._report = report
        self.step_store.clear()
        self._visible_steps = []
        self._set_detail("")
        self.html_button.set_sensitive(bool(report and report.report_html))
        self.folder_button.set_sensitive(bool(report and report.artifact_dir))
        if report is None:
            self.heading.set_markup("<b>Select a run to inspect its report</b>")
            self.summary.set_text("Run details, step evidence and diagnostics appear here.")
            return
        import html
        self.heading.set_markup("<b>%s</b>" % html.escape(report.plan_name or report.run_id))
        started = report.started_at or "Unknown"
        finished = report.finished_at or "In progress"
        self.summary.set_text(
            "%s    •    %d passed    •    %d failed    •    %d skipped    •    %d not run\n"
            "Started: %s    Finished: %s\nRun: %s%s" % (
                report.status, report.passed, report.failed, report.skipped,
                report.not_run, started, finished, report.run_id,
                "\nLegacy report: source plan path was not recorded; name matching may be ambiguous."
                if report.legacy_identity else "",
            )
        )
        self._populate()
        if report.diagnostics and not report.steps:
            self._set_detail("RUN DIAGNOSTICS\n\n" + "\n".join(report.diagnostics))

    def _populate(self):
        report = self._report
        old_node = None
        model, iterator = self.step_tree.get_selection().get_selected()
        if iterator is not None:
            index = model.get_path(iterator).get_indices()[0]
            if index < len(self._visible_steps):
                old_node = self._visible_steps[index].node_id
        self.step_store.clear()
        self._visible_steps = []
        if report is None:
            return
        wanted = {
            "Failed": "FAILED", "Passed": "PASSED", "Running": "RUNNING",
            "Not run": "NOT RUN", "Skipped": "SKIPPED", "Cancelled": "CANCELLED",
        }.get(self.step_filter.get_active_text())
        query = self.search.get_text().strip().casefold()
        for number, step in enumerate(report.steps, 1):
            if wanted is not None and step.status != wanted:
                continue
            if query and not any(query in str(value).casefold() for value in (
                step.name, step.step_id, step.node_id, step.error,
            )):
                continue
            self._visible_steps.append(step)
            self.step_store.append((str(number), step.status, step.name))
        if old_node is not None:
            for index, step in enumerate(self._visible_steps):
                if step.node_id == old_node:
                    self.step_tree.get_selection().select_path(index)
                    return
        if self._visible_steps:
            index = next((i for i, step in enumerate(self._visible_steps)
                          if step.status == "FAILED"), 0)
            self.step_tree.get_selection().select_path(index)
        else:
            self._set_detail("No steps match the current filter." if report.steps else
                             "No step evidence was recorded for this run." +
                             ("\n\n" + "\n".join(report.diagnostics) if report.diagnostics else ""))

    def select_step(self, node_id: str):
        """Drill into a step from the project batch failure summary."""
        if not self._report:
            return
        self.search.set_text("")
        self.step_filter.set_active(0)
        for index, step in enumerate(self._visible_steps):
            if step.node_id == node_id:
                self.step_tree.get_selection().select_path(index)
                self.step_tree.scroll_to_cell(Gtk.TreePath.new_from_indices([index]))
                return

    def _show_step(self):
        model, iterator = self.step_tree.get_selection().get_selected()
        if iterator is None:
            return
        index = model.get_path(iterator).get_indices()[0]
        if index >= len(self._visible_steps):
            return
        step = self._visible_steps[index]
        lines = [
            step.name, "─" * 32, "",
            "RESULT   " + step.status,
            "NODE     " + step.node_id,
            "ACTION   " + step.step_id,
        ]
        if step.error:
            lines.extend(("", "FAILURE", step.error))
        if step.assertions:
            lines.extend(("", "ASSERTIONS", "─" * 32))
            for assertion in step.assertions:
                lines.append("[%s] %s" % (
                    "PASS" if assertion.get("passed") else "FAIL",
                    assertion.get("assertion") or assertion.get("message") or "Assertion",
                ))
                for key in ("message", "expected", "actual", "component_id"):
                    if key in assertion:
                        lines.append("  %s: %s" % (key.replace("_", " ").title(), assertion[key]))
                for evidence in assertion.get("evidence") or ():
                    if isinstance(evidence, dict):
                        lines.append("  %s: %s" % (
                            str(evidence.get("role", "Evidence")).title(),
                            evidence.get("value", evidence.get("path", evidence.get("type", ""))),
                        ))
                lines.append("")
        elif not step.error:
            lines.extend(("", "No assertion evidence recorded for this step."))
        self._set_detail("\n".join(lines))

    def _set_detail(self, value):
        self.detail.get_buffer().set_text(value)

    def _open_uri(self, uri):
        try:
            Gtk.show_uri_on_window(self._parent_window, uri, Gdk.CURRENT_TIME)
        except Exception:
            webbrowser.open(uri)

    def _open_html(self, *_args):
        if self._report and self._report.report_html:
            self._open_uri(self._report.report_html.resolve().as_uri())

    def _open_folder(self, *_args):
        if self._report and self._report.artifact_dir:
            self._open_uri(self._report.artifact_dir.resolve().as_uri())
