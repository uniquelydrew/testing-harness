from pathlib import Path

import pytest

from automation_harness.core.object_capture import ObjectCaptureService
from automation_harness.reporting.artifacts import RunArtifacts
from automation_harness.runtime_paths import ensure_external_runtime_path, runtime_path
from automation_harness.runner.cli import build_parser


def test_default_runtime_paths_use_configured_external_root(monkeypatch, tmp_path):
    root = tmp_path / "runtime"
    monkeypatch.setenv("AUTOMATION_HARNESS_RUNTIME_DIR", str(root))

    assert runtime_path("runs") == root / "runs"
    assert ObjectCaptureService().diagnostic_path == root / "logs" / "object-capture.jsonl"
    parsed = build_parser().parse_args(["selftest"])
    assert parsed.runs_dir == root / "runs"


def test_generated_artifacts_reject_a_git_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)

    with pytest.raises(ValueError, match="outside a Git checkout"):
        ensure_external_runtime_path(checkout / "runs")
    with pytest.raises(ValueError, match="outside a Git checkout"):
        RunArtifacts.create(checkout / "runs", "example")
