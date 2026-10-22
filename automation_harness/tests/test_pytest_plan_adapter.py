"""Regression coverage for optional pytest integration without a live desktop."""

import pytest

from automation_harness.integrations.pytest.plugin import load_selected_project, parse_variable_overrides
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
