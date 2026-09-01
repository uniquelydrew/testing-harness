from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.script_steps import (
    ScriptStepError,
    ScriptStepExecutionError,
    ScriptStepDefinition,
    register_script_step,
    registered_script_step,
)
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_context import TestContext
from automation_harness.core.test_plan import validate_plan
from automation_harness.models.plan import StepCall, TestPlan
from automation_harness.utils.evidence import EvidenceRecorder


def _context(tmp_path: Path) -> TestContext:
    return TestContext(
        backend="live-desktop",
        run_dir=tmp_path,
        evidence=EvidenceRecorder(tmp_path / "events.jsonl"),
        components=ComponentRepository({}),
        capabilities=frozenset(),
        steps=default_step_registry(),
    )


def _write_script_step(tmp_path: Path, step_id: str, script_body: str, outputs: str) -> Path:
    script = tmp_path / "step.py"
    script.write_text(script_body, encoding="utf-8")
    manifest = tmp_path / "step.yaml"
    manifest.write_text(
        f"""version: 1
id: {step_id}
description: Prepare the application environment.
risk: application_control
inputs:
  configuration:
    type: str
    required: true
outputs:
{outputs}
implementation:
  kind: script
  path: step.py
  interpreter: python
  timeout: 5
""",
        encoding="utf-8",
    )
    return manifest


def test_script_step_contract_participates_in_plan_validation_and_dataflow(tmp_path: Path):
    manifest = _write_script_step(
        tmp_path,
        "environment.prepare_alpha",
        """import json, sys
request = json.load(sys.stdin)
assert request["protocol"] == 1
configuration = request["inputs"]["configuration"]
json.dump({"protocol": 1, "outputs": {"workspace": "/tmp/" + configuration, "pid": 4242}}, sys.stdout)
""",
        """  workspace:
    type: str
  pid:
    type: int
""",
    )
    definition = register_script_step(manifest)
    assert {item.name for item in definition.inputs} == {"configuration"}
    assert definition.inputs[0].kind == "keyword_only"
    assert definition.output_names == frozenset({"workspace", "pid"})

    invalid_plan = TestPlan(
        "missing-script-input",
        steps=(StepCall("setup", "environment.prepare_alpha"),),
    )
    assert validate_plan(invalid_plan, default_step_registry()) == [
        "setup: missing required input 'configuration'"
    ]

    ctx = _context(tmp_path)
    invocation = ctx.run_step_detailed(
        "environment.prepare_alpha",
        configuration="integration",
        bind_outputs={"workspace": "workspace", "pid": "application_pid"},
    )
    assert invocation.outputs == {"workspace": "/tmp/integration", "pid": 4242}
    assert ctx.globals.snapshot()["workspace"] == "/tmp/integration"
    assert ctx.globals.snapshot()["application_pid"] == 4242

    metadata = registered_script_step("environment.prepare_alpha")
    assert metadata.script_sha256
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert "script_step_started" in [event["event"] for event in events]
    assert "script_step_finished" in [event["event"] for event in events]


def test_script_step_rejects_outputs_that_violate_contract(tmp_path: Path):
    manifest = _write_script_step(
        tmp_path,
        "environment.prepare_bad_output",
        """import json, sys
json.load(sys.stdin)
json.dump({"protocol": 1, "outputs": {"pid": "not-an-integer"}}, sys.stdout)
""",
        """  pid:
    type: int
""",
    )
    register_script_step(manifest)
    ctx = _context(tmp_path)
    with pytest.raises(ScriptStepExecutionError, match="output 'pid' expects int"):
        ctx.run_step("environment.prepare_bad_output", configuration="integration")


def test_script_step_rejects_optional_declared_outputs(tmp_path: Path):
    manifest = _write_script_step(
        tmp_path,
        "environment.prepare_optional_output",
        """import json, sys
json.load(sys.stdin)
json.dump({"protocol": 1, "outputs": {}}, sys.stdout)
""",
        """  workspace:
    type: str
    required: false
""",
    )
    with pytest.raises(ScriptStepError, match="cannot be optional"):
        ScriptStepDefinition.load(manifest)
