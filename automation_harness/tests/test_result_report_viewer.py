"""GTK3 smoke tests for the shared results inspector (xvfb-compatible)."""
import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.gui.result_report_viewer import ResultReportViewer
from automation_harness.reporting.run_reports import RunReport, ReportStep


@pytest.fixture
def viewer():
    if not Gtk.init_check()[0]:
        pytest.skip("GTK display unavailable")
    window = Gtk.Window()
    widget = ResultReportViewer(parent_window=window)
    window.add(widget)
    window.show_all()
    yield widget
    window.destroy()


def _report(tmp_path):
    return RunReport(
        run_id="run-1",
        plan_name="Demo Plan",
        status="FAILED",
        started_at="2026-11-03T10:00:00Z",
        finished_at="2026-11-03T10:01:00Z",
        passed=1, failed=1, skipped=0,
        artifact_dir=tmp_path,
        source_plan_path=tmp_path / "demo.ahplan",
        steps=(
            ReportStep("one", "gui.click", "Open File", "PASSED"),
            ReportStep("two", "gui.select", "Select Site", "FAILED", "No matching item"),
        ),
    )


def test_report_viewer_filters_and_drills_into_failures(viewer, tmp_path):
    viewer.set_report(_report(tmp_path))
    assert len(viewer.step_store) == 2
    assert "Demo Plan" in viewer.heading.get_label()
    assert viewer.folder_button.get_sensitive()
    assert not viewer.html_button.get_sensitive()

    viewer.step_filter.set_active(1)
    assert len(viewer.step_store) == 1
    assert viewer.step_store[0][2] == "Select Site"

    viewer.select_step("one")
    assert viewer.step_filter.get_active_text() == "All steps"
    model, iterator = viewer.step_tree.get_selection().get_selected()
    assert iterator is not None
    assert model[iterator][2] == "Open File"

    viewer.search.set_text("select site")
    assert len(viewer.step_store) == 1
    assert viewer.step_store[0][2] == "Select Site"


def test_report_viewer_reset_disables_artifact_buttons(viewer, tmp_path):
    viewer.set_report(_report(tmp_path))
    viewer.set_report(None)
    assert viewer.report is None
    assert len(viewer.step_store) == 0
    assert not viewer.html_button.get_sensitive()
    assert not viewer.folder_button.get_sensitive()

def test_test_plan_completion_selects_finished_report_before_presenting():
    from types import SimpleNamespace
    from automation_harness.authoring.gui.plan_window import TestPlanWindow

    actions = []
    class Button:
        def set_sensitive(self, value):
            actions.append(("button", value))
    class Notebook:
        def set_current_page(self, index):
            actions.append(("page", index))
    class Window:
        def present(self):
            actions.append(("present", True))
    gui = SimpleNamespace(
        run_button=Button(), plan_notebook=Notebook(), window=Window(),
        _run_minimized_windows=[],
        refresh_run_reports=lambda *, preferred_id: actions.append(("report", preferred_id)),
        set_status=lambda message: actions.append(("status", message)),
    )
    result = SimpleNamespace(run_id="latest-run", exit_code=1, passed=4, failed=1)
    assert TestPlanWindow._run_finished(gui, result, None) is False
    assert actions.index(("report", "latest-run")) < actions.index(("present", True))
    assert ("page", 1) in actions


def test_project_completion_selects_finished_batch_and_drilldown():
    from types import SimpleNamespace
    from automation_harness.authoring.gui.project_window import ProjectWindow

    actions = []
    class Button:
        def set_sensitive(self, value):
            actions.append(("button", value))
    class Notebook:
        def set_current_page(self, index):
            actions.append(("page", index))
    class Selection:
        def select_path(self, path):
            actions.append(("select_report", path))
    class Tree:
        def get_selection(self):
            return Selection()
    class Window:
        def present(self):
            actions.append(("present", True))
    gui = SimpleNamespace(
        _run_active=True, run_selected_button=Button(), run_all_button=Button(),
        project_notebook=Notebook(), plan_run_tree=Tree(), _visible_plan_records=[0],
        window=Window(), _run_minimized_windows=[],
        refresh_runs=lambda *, preferred_batch_id: actions.append(("batch", preferred_batch_id)),
        set_status=lambda message: actions.append(("status", message)),
    )
    batch = SimpleNamespace(
        batch_id="latest-batch", plans=(SimpleNamespace(),),
        status="failed", passed=0, failed=1,
    )
    assert ProjectWindow._batch_finished(gui, batch, None) is False
    assert ("batch", "latest-batch") in actions
    assert ("page", 1) in actions
    assert ("select_report", 0) in actions
    assert actions.index(("select_report", 0)) < actions.index(("present", True))
