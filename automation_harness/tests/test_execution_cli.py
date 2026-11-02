from __future__ import annotations

import json

from automation_harness.core.test_plan import save_plan
from automation_harness.models.plan import StepCall, TestPlan
from automation_harness.runner import cli, live_cli
from automation_harness.runner.cli import main


def _plan(path, name="CLI test"):
    plan = TestPlan(
        name=name,
        steps=(StepCall("check", "validation.equal", {"name": "value", "actual": 1, "expected": 1}),),
    )
    save_plan(plan, path)
    return path


def _project(path, plans):
    path.write_text(
        "version: 2\nname: CLI project\ntest_plans:\n" +
        "".join("  - %s\n" % item.name for item in plans) +
        "step_registries: []\nobject_repositories: []\n",
        encoding="utf-8",
    )
    return path


def _reference_options(runs_dir):
    return ["--backend", "reference", "--reference-mode", "headless", "--runs-dir", str(runs_dir)]


def test_test_run_is_an_explicit_alias_for_single_plan_execution(tmp_path, capsys):
    plan = _plan(tmp_path / "single.ahplan")

    assert main(["test", "run", str(plan), *_reference_options(tmp_path / "runs")]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["exit_code"] == 0


def test_batch_and_project_run_persist_and_expose_project_execution(tmp_path, capsys):
    first = _plan(tmp_path / "first.ahplan", "First")
    second = _plan(tmp_path / "second.ahplan", "Second")
    project = _project(tmp_path / "project.ahproject", (first, second))
    runs_dir = tmp_path / "runs"

    assert main(["batch", "run", str(project), str(second), *_reference_options(runs_dir)]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["status"] == "passed"
    assert [item["plan_name"] for item in selected["plans"]] == ["Second"]

    assert main(["project", "run", str(project), *_reference_options(runs_dir)]) == 0
    all_plans = json.loads(capsys.readouterr().out)
    assert all_plans["passed"] == 2

    assert main(["project", "history", str(project), "--runs-dir", str(runs_dir), "--json"]) == 0
    history = json.loads(capsys.readouterr().out)
    assert len(history) == 2


def test_batch_rejects_a_plan_not_owned_by_the_project(tmp_path, capsys):
    member = _plan(tmp_path / "member.ahplan")
    outsider = _plan(tmp_path / "outsider.ahplan")
    project = _project(tmp_path / "project.ahproject", (member,))

    assert main(["batch", "run", str(project), str(outsider), *_reference_options(tmp_path / "runs")]) == 2
    assert "must be members" in capsys.readouterr().err


def test_live_entrypoint_accepts_script_steps_for_each_execution_scope(tmp_path):
    live_cli._install_cli_policy(cli)
    parser = cli.build_parser()
    script = tmp_path / "custom.ahstep"

    assert parser.parse_args(["test", "run", "test.ahplan", "--script-step", str(script)]).script_step == [script]
    assert parser.parse_args(["batch", "run", "project.ahproject", "test.ahplan", "--script-step", str(script)]).script_step == [script]
    assert parser.parse_args(["project", "run", "project.ahproject", "--script-step", str(script)]).script_step == [script]
