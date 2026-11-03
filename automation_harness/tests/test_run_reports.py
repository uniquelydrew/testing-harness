"""Pure-Python regression coverage for the shared GTK report data layer."""
import json
from pathlib import Path

from automation_harness.authoring.project_runs import ProjectPlanRun
from automation_harness.reporting.run_reports import (
    list_plan_reports,
    load_run_report,
)


def _run(directory, *, plan_name="Demo", source=None, started="2026-11-03T12:00:00Z"):
    directory.mkdir(parents=True)
    data = {
        "run_id": directory.name,
        "bundle": plan_name,
        "source_plan_path": str(source) if source else None,
        "started_at": started,
        "finished_at": started,
        "exit_code": 1,
        "passed": 1,
        "failed": 1,
        "skipped": 0,
        "validation_errors": ["validation warning"],
    }
    (directory / "run.json").write_text(json.dumps(data), encoding="utf-8")
    return directory


def test_plan_history_matches_canonical_path_even_with_duplicate_plan_names(tmp_path):
    runs = tmp_path / "runs"
    first = tmp_path / "first.ahplan"
    second = tmp_path / "second.ahplan"
    _run(runs / "first", source=first)
    _run(runs / "second", source=second)
    _run(runs / "legacy", started="2026-11-02T12:00:00Z")
    _run(runs / "irrelevant", plan_name="Other")

    reports = list_plan_reports(runs, first, "Demo")

    assert [item.run_id for item in reports] == ["first", "legacy"]
    assert reports[0].source_plan_path == first.resolve()
    assert reports[0].legacy_identity is False
    assert reports[1].legacy_identity is True


def test_run_reader_uses_execution_state_and_historic_labels(tmp_path):
    run = _run(tmp_path / "runs" / "report")
    (run / "step_labels.json").write_text(json.dumps({
        "n1": {"name": "Original authored label", "step_id": "gui.click"},
        "n2": {"name": "Second step", "step_id": "gui.select"},
    }), encoding="utf-8")
    (run / "events.jsonl").write_text(
        '{"event":"plan_step_started","node_id":"n2","step_id":"gui.select"}\n'
        'not JSON\n'
        '{"event":"plan_step_failed","node_id":"n2","error":"target not found"}\n'
        '{"event":"assertion","node_id":"n2","passed":false,"expected":"open","actual":"closed"}\n'
        '{"event":"plan_step_started","node_id":"n1","step_id":"gui.click"}\n',
        encoding="utf-8",
    )
    (run / "execution_state.json").write_text(json.dumps({
        "steps": {
            "n1": {"step_id": "gui.click", "status": "passed"},
            "n2": {"step_id": "gui.select", "status": "failed", "error": "target not found"},
            "n3": {"step_id": "gui.wait", "status": "pending"},
        }
    }), encoding="utf-8")

    report = load_run_report(run)

    assert report.status == "FAILED"
    assert [step.node_id for step in report.steps] == ["n2", "n1", "n3"]
    assert [step.status for step in report.steps] == ["FAILED", "PASSED", "NOT RUN"]
    assert report.steps[0].name == "Second step"
    assert report.steps[0].assertions[0]["actual"] == "closed"
    assert report.steps[1].name == "Original authored label"
    assert report.not_run == 1
    assert report.diagnostics == ("validation warning",)


def test_missing_artifact_preserves_project_failure_diagnostic(tmp_path):
    plan = tmp_path / "plan.ahplan"
    record = ProjectPlanRun(
        plan_path=str(plan), plan_name="Plan", status="failed",
        started_at="now", error="FileNotFoundError: source missing", exit_code=2,
    )
    report = load_run_report(None, fallback_record=record)

    assert report.plan_name == "Plan"
    assert report.source_plan_path == plan.resolve()
    assert report.status == "FAILED"
    assert report.artifact_dir is None
    assert report.steps == ()
    assert report.diagnostics == ("FileNotFoundError: source missing",)


def test_corrupt_and_missing_run_metadata_are_ignored(tmp_path):
    base = tmp_path / "runs"
    empty = base / "empty"
    empty.mkdir(parents=True)
    (empty / "run.json").write_text("{invalid", encoding="utf-8")

    assert load_run_report(empty) is None
    assert list_plan_reports(base, tmp_path / "plan.ahplan", "Demo") == ()
    assert list_plan_reports(tmp_path / "missing", tmp_path / "plan.ahplan", "Demo") == ()
