"""Opt-in pytest collector for first-class declarative .ahplan test items.

pytest owns collection and suite results; the harness owns every plan step,
object resolution, backend lifecycle, evidence event, and HTML report.
"""

import json
from pathlib import Path

import pytest

from automation_harness.authoring.project import AuthoringProject
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.backends.reference import ReferenceBackend
from automation_harness.core.test_plan import load_plan
from automation_harness.integrations.pytest.lifecycle import run_command
from automation_harness.runner.plan_execution import execute_plan
from automation_harness.runner.plan_resources import resolve_plan_resources
from automation_harness.runtime_paths import runtime_path


class PlanRunFailure(AssertionError):
    """Failed harness plan with its authoritative run diagnostics."""


def parse_variable_overrides(values):
    """Parse repeated NAME=JSON (or NAME=text) options, rejecting ambiguous keys."""
    overrides = {}
    for value in values:
        name, separator, raw = value.partition("=")
        if not separator or not name or name.strip() != name or "." in name:
            raise ValueError("invalid --ah-var %r: expected top-level NAME=VALUE" % value)
        if name in overrides:
            raise ValueError("duplicate --ah-var name: %s" % name)
        try:
            overrides[name] = json.loads(raw)
        except ValueError:
            overrides[name] = raw
    return overrides


def pytest_addoption(parser):
    group = parser.getgroup("automation-harness", "Automation Harness .ahplan integration")
    group.addoption(
        "--ah-backend", choices=("live-desktop", "reference"), default="live-desktop",
        help="Execution backend for collected .ahplan tests (default: live-desktop)",
    )
    group.addoption("--ah-project", default=None, metavar="PATH", help="Optional .ahproject owning the collected plans")
    group.addoption("--ah-runs-dir", default=None, metavar="PATH", help="Directory for authoritative harness run artifacts")
    group.addoption("--ah-var", action="append", default=[], metavar="NAME=VALUE", help="Override a top-level plan variable (JSON values accepted)")
    group.addoption("--ah-repository-mode", choices=("linked", "portable", "locked"), default=None,
                    help="Override the TestPlan repository execution mode")
    group.addoption(
        "--ah-reference-mode", choices=("gui", "headless"), default="gui",
        help="Synthetic reference backend mode",
    )
    group.addoption(
        "--ah-reference-display", choices=("virtual", "native", "auto"), default="virtual",
        help="Display policy for reference GUI mode",
    )
    group.addoption("--ah-setup-command", default=None, metavar="COMMAND",
                    help="Optional executable and arguments run before each plan (no shell)")
    group.addoption("--ah-teardown-command", default=None, metavar="COMMAND",
                    help="Optional executable and arguments run after each plan, even on failure")
    group.addoption("--ah-lifecycle-timeout", type=int, default=90, metavar="SECONDS",
                    help="Timeout per lifecycle command (default: 90 seconds)")


def pytest_configure(config):
    if config.getoption("ah_lifecycle_timeout") < 1:
        raise pytest.UsageError("--ah-lifecycle-timeout must be a positive number of seconds")
    config.addinivalue_line("markers", "ahplan: declarative Automation Harness TestPlan")
    config.addinivalue_line("markers", "gui: requires a graphical execution target")
    # A shared native desktop cannot safely service multiple xdist workers.
    workers = config.getoption("numprocesses", default=None)
    if config.getoption("ah_backend") == "live-desktop" and workers not in (None, 0, 1, "0", "1"):
        raise pytest.UsageError("parallel live-desktop .ahplan execution is unsupported; use one worker per isolated desktop")


def pytest_collect_file(path, parent):
    if getattr(path, "ext", "").lower() == ".ahplan":
        return HarnessPlanFile.from_parent(parent, fspath=path)
    return None


class HarnessPlanFile(pytest.File):
    def collect(self):
        plan_path = Path(str(self.fspath))
        try:
            load_plan(plan_path)
        except Exception as exc:
            raise pytest.Collector.CollectError("%s: %s: %s" % (plan_path, type(exc).__name__, exc))
        # File stem is stable even if a user edits the display name in the plan.
        item = HarnessPlanItem.from_parent(self, name=plan_path.stem)
        item.add_marker("ahplan")
        if self.config.getoption("ah_backend") == "live-desktop":
            item.add_marker("gui")
        yield item


def load_selected_project(project_option, plan_path):
    """Optional project selection, without changing Project Manager batch semantics."""
    if not project_option:
        return None
    project = AuthoringProject.load(Path(project_option))
    if Path(plan_path).resolve() not in project.test_plans:
        raise ValueError("plan %s is not a member of project %s" % (plan_path, project.name))
    return project


def result_class(exit_code):
    """Stable CI metadata without inventing pytest status semantics."""
    return {
        0: "passed",
        1: "step_failure",
        2: "qualification_failure",
        3: "runtime_error",
    }.get(exit_code, "unknown_failure")


class HarnessPlanItem(pytest.Item):
    def runtest(self):
        options = self.config
        path = Path(str(self.parent.fspath)).resolve()
        plan = load_plan(path)
        project = load_selected_project(options.getoption("ah_project"), path)
        resources = resolve_plan_resources(plan, path, project=project)
        overrides = parse_variable_overrides(options.getoption("ah_var"))
        backend_name = options.getoption("ah_backend")
        if backend_name == "reference":
            backend = ReferenceBackend(
                gui=options.getoption("ah_reference_mode") == "gui",
                display_mode=options.getoption("ah_reference_display"),
            )
        else:
            backend = LiveDesktopBackend()
        configured_runs = options.getoption("ah_runs_dir")
        runs_dir = Path(configured_runs).expanduser().resolve() if configured_runs else runtime_path("runs")

        result = None
        execution_error = None
        teardown_error = None
        cwd = Path(str(options.rootdir))
        timeout = options.getoption("ah_lifecycle_timeout")
        try:
            setup = options.getoption("ah_setup_command")
            if setup:
                completed = run_command(setup, phase="setup", cwd=cwd, timeout=timeout)
                if completed.stdout:
                    self.add_report_section("call", "harness setup stdout", completed.stdout)
                if completed.stderr:
                    self.add_report_section("call", "harness setup stderr", completed.stderr)
            result = execute_plan(
                plan, backend, runs_dir=runs_dir,
                variable_overrides=overrides, component_repository=resources.repository,
                reusable_steps=resources.reusable_steps,
                execution_mode=options.getoption("ah_repository_mode"),
            )
        except Exception as exc:
            execution_error = exc
        finally:
            # Independent of plan.step ordering and execute_plan()'s early-stop policy.
            # This also runs if setup, backend execution, or an assertion fails.
            teardown = options.getoption("ah_teardown_command")
            if teardown:
                try:
                    completed = run_command(teardown, phase="teardown", cwd=cwd, timeout=timeout)
                    if completed.stdout:
                        self.add_report_section("call", "harness teardown stdout", completed.stdout)
                    if completed.stderr:
                        self.add_report_section("call", "harness teardown stderr", completed.stderr)
                except Exception as exc:
                    teardown_error = exc

        if result is not None:
            self.user_properties.extend((
                ("harness_run_id", result.run_id),
                ("harness_exit_code", str(result.exit_code)),
                ("harness_result_class", result_class(result.exit_code)),
            ))
            if result.artifact_dir is not None:
                artifact_dir = Path(result.artifact_dir)
                self.user_properties.extend((
                    ("harness_artifact_dir", str(artifact_dir)),
                    ("harness_report_html", str(artifact_dir / "report.html")),
                ))
        else:
            self.user_properties.append(("harness_result_class", "adapter_error"))
        if teardown_error is not None:
            self.user_properties.append(("harness_teardown", "failed"))

        if execution_error is not None:
            if teardown_error is not None:
                raise PlanRunFailure(
                    "Plan adapter error: %s: %s\nTeardown also failed: %s" % (
                        type(execution_error).__name__, execution_error, teardown_error,
                    )
                ) from execution_error
            raise execution_error
        if result is not None and result.exit_code != 0:
            details = "\n".join(result.validation_errors) or "No further diagnostic was returned"
            if teardown_error is not None:
                details += "\nTeardown also failed: %s" % teardown_error
            raise PlanRunFailure(
                "Harness plan %s failed (%s, exit code %s)\n%s\nArtifacts: %s" % (
                    path.name, result_class(result.exit_code), result.exit_code,
                    details, result.artifact_dir,
                )
            )
        if teardown_error is not None:
            raise PlanRunFailure("Harness teardown failed after plan %s: %s" % (
                path.name, teardown_error,
            ))

    def repr_failure(self, excinfo):
        if isinstance(excinfo.value, PlanRunFailure):
            return str(excinfo.value)
        return super(HarnessPlanItem, self).repr_failure(excinfo)

    def reportinfo(self):
        return self.parent.fspath, 0, "Harness plan: %s" % self.name
