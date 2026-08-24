from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

from automation_harness.backends.base import ExecutionBackend
from automation_harness.models.run import RunResult, utc_now
from automation_harness.reporting.artifacts import RunArtifacts
from automation_harness.runner.bundle import TestBundle
from automation_harness.runner.validator import validate_bundle


def execute_bundle(
    bundle: TestBundle,
    backend: ExecutionBackend,
    *,
    runs_dir: Path,
    verbose: bool = False,
    variable_overrides: dict[str, object] | None = None,
) -> RunResult:
    initial_globals = dict(bundle.variables or {})
    if variable_overrides:
        initial_globals.update(variable_overrides)

    artifacts = RunArtifacts.create(runs_dir, bundle.name)
    result = RunResult(
        run_id=artifacts.root.name,
        backend=backend.name,
        bundle=bundle.name,
        started_at=utc_now(),
        artifact_dir=artifacts.root,
    )
    recorder = artifacts.recorder()
    recorder.record("run_started", bundle=bundle.name, backend=backend.name)

    issues = validate_bundle(bundle, backend_capabilities=backend.capabilities)
    preflight = backend.preflight_issues()
    if issues or preflight:
        result.validation_errors = [str(issue) for issue in issues] + [f"backend preflight: {issue}" for issue in preflight]
        result.exit_code = 2
        recorder.record("validation_failed", issues=result.validation_errors)
        return _finalize_run(result, artifacts, recorder, bundle, backend, initial_globals)

    try:
        backend_env = backend.start(run_dir=artifacts.root)
        health = backend.health_check()
        recorder.record("backend_health", healthy=health.healthy, details=health.details)
        if not health.healthy:
            raise RuntimeError(f"backend failed health check: {health.details}")

        env = os.environ.copy()
        env.update(backend_env)
        env["AUTOMATION_HARNESS_RUN_DIR"] = str(artifacts.root)
        env["AUTOMATION_HARNESS_CAPABILITIES"] = json.dumps(sorted(backend.capabilities))
        env["AUTOMATION_HARNESS_STEP_LIBRARIES"] = json.dumps([str(path) for path in bundle.step_libraries])
        env["AUTOMATION_HARNESS_BUNDLE_ROOT"] = str(bundle.root)
        env["AUTOMATION_HARNESS_GLOBALS"] = json.dumps(initial_globals)
        if bundle.components is not None:
            env["AUTOMATION_HARNESS_COMPONENTS"] = str(bundle.components)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        package_parent = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, env.get("PYTHONPATH", "")]))

        args = [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "automation_harness.runner.pytest_plugin",
            "--junitxml",
            str(artifacts.junit),
            "-q" if not verbose else "-vv",
            *[str(path) for path in bundle.tests],
        ]
        completed = subprocess.run(args, cwd=bundle.root, env=env, text=True, capture_output=True)
        artifacts.stdout.write_text(completed.stdout, encoding="utf-8")
        artifacts.stderr.write_text(completed.stderr, encoding="utf-8")
        result.exit_code = completed.returncode
        total = _count_junit(artifacts.junit, "tests")
        failures = _count_junit(artifacts.junit, "failures")
        errors = _count_junit(artifacts.junit, "errors")
        result.skipped = _count_junit(artifacts.junit, "skipped")
        result.failed = failures + errors
        result.passed = max(0, total - result.failed - result.skipped)
        recorder.record(
            "pytest_finished",
            exit_code=completed.returncode,
            passed=result.passed,
            failed=result.failed,
            skipped=result.skipped,
        )
    except Exception as exc:
        result.exit_code = 3
        result.validation_errors.append(f"runtime error: {type(exc).__name__}: {exc}")
        recorder.record("run_error", error=result.validation_errors[-1])
    finally:
        backend.stop()

    return _finalize_run(result, artifacts, recorder, bundle, backend, initial_globals)


def _finalize_run(
    result: RunResult,
    artifacts: RunArtifacts,
    recorder,
    bundle: TestBundle,
    backend: ExecutionBackend,
    initial_globals: dict[str, object],
) -> RunResult:
    result.finished_at = utc_now()
    recorder.record("run_finished", exit_code=result.exit_code)
    artifacts.write_run_json(result.to_dict())
    artifacts.environment.write_text(
        json.dumps(
            {
                "backend": backend.name,
                "capabilities": sorted(backend.capabilities),
                "python": sys.version,
                "platform": platform.platform(),
                "bundle_root": str(bundle.root),
                "initial_globals": initial_globals,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    status = "PASS" if result.exit_code == 0 else "FAIL"
    lines = [
        f"{status}: {bundle.name}",
        f"Backend: {backend.name}",
        f"Passed: {result.passed}",
        f"Failed: {result.failed}",
        f"Skipped: {result.skipped}",
        f"Exit code: {result.exit_code}",
    ]
    if result.validation_errors:
        lines.append("Errors:")
        lines.extend(f"- {error}" for error in result.validation_errors)
    artifacts.summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def _count_junit(path: Path, attr: str) -> int:
    if not path.is_file():
        return 0
    import xml.etree.ElementTree as ET

    root = ET.parse(path).getroot()
    if root.tag == "testsuites":
        return sum(int(suite.attrib.get(attr, "0")) for suite in root.findall("testsuite"))
    return int(root.attrib.get(attr, "0"))
