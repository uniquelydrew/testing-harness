from pathlib import Path
from types import SimpleNamespace

from automation_harness.authoring import project_runs
from automation_harness.models.plan import TestPlan
from automation_harness.core.test_plan import save_plan


def _project(tmp_path):
    first = tmp_path / "first.ahplan"
    second = tmp_path / "second.ahplan"
    save_plan(TestPlan(name="First"), first)
    save_plan(TestPlan(name="Second"), second)
    return SimpleNamespace(root=tmp_path, test_plans=(first, second), step_registries=()), first, second


def test_project_batch_continues_and_persists_history(tmp_path, monkeypatch):
    project, first, second = _project(tmp_path)
    runs_dir = tmp_path / "runs"
    calls = []

    def fake_execute(plan, _backend, **_kwargs):
        calls.append(plan.name)
        if plan.name == "First":
            return SimpleNamespace(exit_code=1, passed=0, failed=1, artifact_dir=None, validation_errors=["first failed"])
        return SimpleNamespace(exit_code=0, passed=2, failed=0, artifact_dir=None, validation_errors=[])

    monkeypatch.setattr(project_runs, "execute_plan", fake_execute)
    batch = project_runs.execute_project_batch(
        project, tmp_path / "project.ahproject", (first, second), runs_dir=runs_dir, backend_factory=object,
    )

    assert calls == ["First", "Second"]
    assert [item.status for item in batch.plans] == ["failed", "passed"]
    assert batch.finished_at is not None
    history = project_runs.load_batches(runs_dir, tmp_path / "project.ahproject")
    assert history == (batch,)


def test_history_ignores_corrupt_records(tmp_path):
    project_path = tmp_path / "project.ahproject"
    directory = project_runs.history_directory(tmp_path / "runs", project_path)
    directory.mkdir(parents=True)
    (directory / "bad.json").write_text("not json", encoding="utf-8")

    assert project_runs.load_batches(tmp_path / "runs", project_path) == ()


def test_project_batch_records_missing_plan_and_continues(tmp_path, monkeypatch):
    project, _first, second = _project(tmp_path)
    calls = []

    def fake_execute(plan, _backend, **_kwargs):
        calls.append(plan.name)
        return SimpleNamespace(exit_code=0, passed=1, failed=0, artifact_dir=None, validation_errors=[])

    monkeypatch.setattr(project_runs, "execute_plan", fake_execute)
    batch = project_runs.execute_project_batch(
        project, tmp_path / "project.ahproject", (tmp_path / "missing.ahplan", second),
        runs_dir=tmp_path / "runs", backend_factory=object,
    )

    assert calls == ["Second"]
    assert batch.plans[0].status == "failed"
    assert "FileNotFoundError" in batch.plans[0].error
    assert batch.plans[1].status == "passed"


def test_failed_steps_returns_step_and_assertion_evidence(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.mkdir()
    (artifact / "events.jsonl").write_text(
        '{"event":"plan_step_failed","node_id":"login","step":"gui.click","error":"not found"}\n'
        '{"event":"assertion","node_id":"login","passed":false,"expected":"visible","actual":"hidden"}\n',
        encoding="utf-8",
    )
    record = project_runs.ProjectPlanRun("plan.ahplan", "Plan", "failed", "now", artifact_dir=str(artifact))

    assert project_runs.failed_steps(record) == ({
        "node_id": "login", "step": "gui.click", "error": "not found",
        "assertions": [{"event": "assertion", "node_id": "login", "passed": False, "expected": "visible", "actual": "hidden"}],
    },)
