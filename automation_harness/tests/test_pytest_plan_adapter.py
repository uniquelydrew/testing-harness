"""Regression coverage for optional pytest integration without a live desktop."""

import pytest

from automation_harness.integrations.pytest.plugin import load_selected_project, parse_variable_overrides, result_class
from automation_harness.integrations.pytest.lifecycle import run_command, LifecycleError
import sys
import shlex
import json
from automation_harness.core.test_plan import load_plan
from automation_harness.runner.plan_resources import resolve_plan_resources

pytest_plugins = ("pytester",)


PLAN = """name: T1789
version: 1
variables: {}
objects: {}
step_definitions: {}
steps: []
"""


def test_cli_disables_assertion_rewriting_before_loading_adapter(monkeypatch):
    """Pytest must not bypass the Python 3.6 source compatibility loader."""
    from automation_harness.compat.pytest_cli import main

    captured = []

    def fake_main(arguments):
        captured.extend(arguments)
        return 0

    monkeypatch.setattr(pytest, "main", fake_main)
    assert main(["--collect-only", "-q"]) == 0
    assert captured[:3] == [
        "--assert=plain", "-p", "automation_harness.integrations.pytest.plugin",
    ]
    assert captured[-2:] == ["--collect-only", "-q"]


def test_variable_overrides_parse_json_and_strings():
    assert parse_variable_overrides(["site=12", "enabled=true", "camera=North Gate"]) == {
        "site": 12, "enabled": True, "camera": "North Gate",
    }


@pytest.mark.parametrize("values", [["bad"], ["a.b=1"], ["x=1", "x=2"], [" =value"]])
def test_invalid_variable_override_rejected(values):
    with pytest.raises(ValueError):
        parse_variable_overrides(values)


def test_plan_resource_resolution_without_project(tmp_path):
    path = tmp_path / "T1789.ahplan"
    path.write_text(PLAN, encoding="utf-8")
    plan = load_plan(path)
    resolved = resolve_plan_resources(plan, path)
    assert not resolved.repository.components
    assert not resolved.reusable_steps


def test_project_resource_resolver_preserves_batch_semantics(tmp_path):
    from automation_harness.authoring.project import AuthoringProject
    path = tmp_path / "T1789.ahplan"
    path.write_text(PLAN, encoding="utf-8")
    project = AuthoringProject(name="ERSA", root=tmp_path)
    # Project Manager previously allowed supplied plan paths; the resolver must
    # not silently impose a new membership restriction on that existing caller.
    resolved = resolve_plan_resources(load_plan(path), path, project=project)
    assert not resolved.repository.components


def test_pytest_rejects_nonmember_project(tmp_path, monkeypatch):
    from automation_harness.authoring.project import AuthoringProject
    from automation_harness.integrations.pytest import plugin

    path = tmp_path / "T1789.ahplan"
    project = AuthoringProject(name="ERSA", root=tmp_path)
    monkeypatch.setattr(plugin.AuthoringProject, "load", lambda _: project)
    with pytest.raises(ValueError, match="not a member"):
        load_selected_project(str(tmp_path / "project.ahproject"), path)


def test_pytest_collects_ahplan_without_execution(pytester):
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "T1789.ahplan"
    plan.write_text(PLAN, encoding="utf-8")
    result = pytester.runpytest_inprocess("--collect-only", "-q", str(plan))
    result.stdout.fnmatch_lines(["*T1789.ahplan::T1789*", "*1 test collected*"])
    assert result.ret == 0


def test_invalid_plan_is_collection_error(pytester):
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "T1789.ahplan"
    plan.write_text("invalid: [", encoding="utf-8")
    result = pytester.runpytest_inprocess("--collect-only", "-q", str(plan))
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*ERROR collecting*"])


def test_collected_item_delegates_to_harness_and_exports_properties(pytester, monkeypatch):
    """One saved plan is one pytest item, backed by one harness execution."""
    from types import SimpleNamespace
    from automation_harness.integrations.pytest import plugin

    calls = []

    def execute_stub(plan, backend, **kwargs):
        calls.append((plan, backend, kwargs))
        return SimpleNamespace(
            exit_code=0, run_id="test-run-123",
            artifact_dir=pytester.path / "artifacts" / "test-run-123",
            validation_errors=[],
        )

    monkeypatch.setattr(plugin, "execute_plan", execute_stub)
    monkeypatch.setattr(plugin, "LiveDesktopBackend", lambda: object())
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "T1789.ahplan"
    plan.write_text(PLAN, encoding="utf-8")
    report = pytester.path / "junit.xml"

    result = pytester.runpytest_inprocess(
        "-q", str(plan), "--junitxml=" + str(report),
        "--ah-runs-dir=" + str(pytester.path / "artifacts"),
    )
    result.assert_outcomes(passed=1)
    assert len(calls) == 1
    assert calls[0][0].name == "T1789"
    assert str(calls[0][2]["runs_dir"]) == str(pytester.path / "artifacts")
    assert "test-run-123" in report.read_text(encoding="utf-8")
    assert "harness_report_html" in report.read_text(encoding="utf-8")


def test_failed_harness_plan_fails_pytest_with_step_diagnostics(pytester, monkeypatch):
    from types import SimpleNamespace
    from automation_harness.integrations.pytest import plugin

    monkeypatch.setattr(plugin, "LiveDesktopBackend", lambda: object())
    monkeypatch.setattr(
        plugin, "execute_plan",
        lambda *args, **kwargs: SimpleNamespace(
            exit_code=1, run_id="test-run-failed",
            artifact_dir=pytester.path / "test-run-failed",
            validation_errors=["step 'watch' failed: AssertionError: missing control"],
        ),
    )
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "T1789.ahplan"
    plan.write_text(PLAN, encoding="utf-8")

    result = pytester.runpytest_inprocess("-q", str(plan))
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*step 'watch' failed*"])


def test_result_categories_are_distinct():
    assert [result_class(i) for i in (0, 1, 2, 3)] == [
        "passed", "step_failure", "qualification_failure", "runtime_error",
    ]


def test_lifecycle_rejects_empty_and_failing_commands(tmp_path):
    with pytest.raises(LifecycleError, match="must not be empty"):
        run_command("   ", phase="setup", cwd=tmp_path, timeout=1)
    command = shlex.quote(sys.executable) + ' -c "raise SystemExit(17)"'
    with pytest.raises(LifecycleError, match="exited 17"):
        run_command(command, phase="teardown", cwd=tmp_path, timeout=10)


def test_collector_assigns_ahplan_marker(pytester):
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    path = pytester.path / "T1789.ahplan"
    path.write_text(PLAN, encoding="utf-8")
    result = pytester.runpytest_inprocess("--collect-only", "-q", "-m", "ahplan", str(path))
    result.stdout.fnmatch_lines(["*1 test collected*"])
    assert result.ret == 0


def test_teardown_runs_after_failed_execution(pytester, monkeypatch):
    from types import SimpleNamespace
    from automation_harness.integrations.pytest import plugin

    monkeypatch.setattr(plugin, "LiveDesktopBackend", lambda: object())
    monkeypatch.setattr(
        plugin, "execute_plan",
        lambda *args, **kwargs: SimpleNamespace(
            exit_code=1, run_id="failed-run", artifact_dir=pytester.path / "failed-run",
            validation_errors=["step failed"],
        ),
    )
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "T1789.ahplan"
    plan.write_text(PLAN, encoding="utf-8")
    marker = pytester.path / "teardown.marker"
    script = pytester.path / "teardown.py"
    script.write_text(
        "from pathlib import Path\nPath(%r).write_text('done')\n" % str(marker),
        encoding="utf-8",
    )
    command = shlex.quote(sys.executable) + " " + shlex.quote(str(script))
    xml = pytester.path / "junit.xml"

    result = pytester.runpytest_inprocess(
        "-q", str(plan), "--ah-teardown-command=" + command,
        "--junitxml=" + str(xml),
    )
    result.assert_outcomes(failed=1)
    assert marker.read_text(encoding="utf-8") == "done"
    assert "step_failure" in xml.read_text(encoding="utf-8")


def test_teardown_runs_even_if_setup_fails(pytester):
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "T1789.ahplan"
    plan.write_text(PLAN, encoding="utf-8")
    setup = pytester.path / "setup.py"
    setup.write_text("raise SystemExit(7)\n", encoding="utf-8")
    marker = pytester.path / "cleaned.marker"
    teardown = pytester.path / "teardown.py"
    teardown.write_text(
        "from pathlib import Path\nPath(%r).write_text('cleaned')\n" % str(marker),
        encoding="utf-8",
    )
    result = pytester.runpytest_inprocess(
        "-q", str(plan),
        "--ah-setup-command=" + shlex.quote(sys.executable) + " " + shlex.quote(str(setup)),
        "--ah-teardown-command=" + shlex.quote(sys.executable) + " " + shlex.quote(str(teardown)),
    )
    result.assert_outcomes(failed=1)
    assert marker.read_text(encoding="utf-8") == "cleaned"


def test_actual_headless_reference_plan_execution(pytester):
    """Real subprocess/backend/registered-step execution; no mocks."""
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "reference-smoke.ahplan"
    plan.write_text(
        """name: reference-pytest-smoke
version: 1
variables: {expected: 42}
objects: {}
step_definitions: {}
steps:
  - id: check-variable
    step: validation.equal
    inputs:
      name: expected
      actual: {$var: expected}
      expected: 42
""", encoding="utf-8",
    )
    runs_dir = pytester.path / "harness-runs"
    xml = pytester.path / "junit.xml"
    result = pytester.runpytest_subprocess(
        "-p", "automation_harness.integrations.pytest.plugin",
        "-q", str(plan), "--ah-backend=reference", "--ah-reference-mode=headless",
        "--ah-runs-dir=" + str(runs_dir), "--junitxml=" + str(xml),
    )
    result.assert_outcomes(passed=1)
    runs = list(runs_dir.glob("*/run.json"))
    assert len(runs) == 1
    data = json.loads(runs[0].read_text(encoding="utf-8"))
    assert data["exit_code"] == 0
    assert (runs[0].parent / "report.html").is_file()
    assert "harness_result_class" in xml.read_text(encoding="utf-8")


def test_reference_plan_failure_produces_step_failure(pytester):
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "reference-failure.ahplan"
    plan.write_text(
        """name: reference-pytest-failure
version: 1
variables: {}
objects: {}
step_definitions: {}
steps:
  - id: failing-assertion
    step: validation.equal
    inputs: {name: fail, actual: 1, expected: 2}
""", encoding="utf-8",
    )
    runs_dir = pytester.path / "harness-runs"
    result = pytester.runpytest_subprocess(
        "-p", "automation_harness.integrations.pytest.plugin",
        "-q", str(plan), "--ah-backend=reference", "--ah-reference-mode=headless",
        "--ah-runs-dir=" + str(runs_dir),
    )
    result.assert_outcomes(failed=1)
    runs = list(runs_dir.glob("*/run.json"))
    assert len(runs) == 1
    assert json.loads(runs[0].read_text(encoding="utf-8"))["exit_code"] == 1


def test_real_qualification_failure_has_distinct_junit_class(pytester):
    """Invalid execution plans must not be mislabeled as step failures."""
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "invalid-execution.ahplan"
    plan.write_text(
        """name: qualification-check
version: 1
variables: {}
objects: {}
step_definitions: {}
steps:
  - id: missing-expected
    step: validation.equal
    inputs: {name: incomplete, actual: 1}
""", encoding="utf-8",
    )
    runs_dir = pytester.path / "runs"
    xml = pytester.path / "junit.xml"
    result = pytester.runpytest_subprocess(
        "-p", "automation_harness.integrations.pytest.plugin",
        "-q", str(plan), "--ah-backend=reference", "--ah-reference-mode=headless",
        "--ah-runs-dir=" + str(runs_dir), "--junitxml=" + str(xml),
    )
    result.assert_outcomes(failed=1)
    records = list(runs_dir.glob("*/run.json"))
    assert len(records) == 1
    assert json.loads(records[0].read_text(encoding="utf-8"))["exit_code"] == 2
    assert "qualification_failure" in xml.read_text(encoding="utf-8")


def test_failed_teardown_does_not_change_successful_harness_result(pytester, monkeypatch):
    """The item fails when cleanup fails, preserving the underlying run result."""
    from types import SimpleNamespace
    from automation_harness.integrations.pytest import plugin

    monkeypatch.setattr(plugin, "LiveDesktopBackend", lambda: object())
    monkeypatch.setattr(
        plugin, "execute_plan",
        lambda *args, **kwargs: SimpleNamespace(
            exit_code=0, run_id="successful-plan", artifact_dir=pytester.path / "successful-plan",
            validation_errors=[],
        ),
    )
    pytester.makeconftest('pytest_plugins = ["automation_harness.integrations.pytest.plugin"]')
    plan = pytester.path / "T1789.ahplan"
    plan.write_text(PLAN, encoding="utf-8")
    script = pytester.path / "teardown.py"
    script.write_text("raise SystemExit(23)\n", encoding="utf-8")
    xml = pytester.path / "junit.xml"
    result = pytester.runpytest_inprocess(
        "-q", str(plan),
        "--ah-teardown-command=" + shlex.quote(sys.executable) + " " + shlex.quote(str(script)),
        "--junitxml=" + str(xml),
    )
    result.assert_outcomes(failed=1)
    recorded = xml.read_text(encoding="utf-8")
    assert "harness_teardown" in recorded
    assert "successful-plan" in recorded
    assert "harness_result_class" in recorded
