from __future__ import annotations

import json

from automation_harness.runner.cli import main


def test_cli_compiles_inspects_and_runs_embedded_artifact(tmp_path, capsys):
    plan = tmp_path / "plan.yaml"
    plan.write_text(
        """name: cli-compiled\nsteps:\n  - id: check\n    step: validation.equal\n    inputs: {name: cli, actual: 1, expected: 1}\n""",
        encoding="utf-8",
    )
    artifact = tmp_path / "compiled.json"
    assert main(["plan", "compile", str(plan), "--output", str(artifact)]) == 0
    capsys.readouterr()
    assert main(["compiled", "inspect", str(artifact)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["instructions"] == 1
    assert main([
        "compiled", "run", str(artifact), "--backend", "reference",
        "--reference-mode", "headless", "--runs-dir", str(tmp_path / "runs"),
    ]) == 0


def test_cli_rejects_artifact_digest_mismatch(tmp_path, capsys):
    artifact = tmp_path / "invalid.json"
    artifact.write_text(json.dumps({"format": "automation-harness/compiled-test-v1", "artifact": {"sha256": "bad"}}), encoding="utf-8")
    assert main(["compiled", "inspect", str(artifact)]) == 2
    assert "ERROR:" in capsys.readouterr().err
