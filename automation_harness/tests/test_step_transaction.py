from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import StepOutputError, default_step_registry
from automation_harness.core.test_context import TestContext
from automation_harness.core.variables import VariableStore
from automation_harness.utils.evidence import EvidenceRecorder


def _ctx(tmp_path: Path) -> TestContext:
    evidence = EvidenceRecorder(tmp_path / "events.jsonl")
    return TestContext(
        backend="reference",
        run_dir=tmp_path,
        evidence=evidence,
        components=ComponentRepository({}),
        capabilities=frozenset({"validation"}),
        steps=default_step_registry(),
        globals=VariableStore(evidence, {"existing": 1}),
    )


def test_invalid_binding_is_rejected_before_step_runs(tmp_path: Path):
    ctx = _ctx(tmp_path)
    with pytest.raises(StepOutputError):
        ctx.run_step(
            "validation.equal",
            "check",
            1,
            1,
            bind_outputs={"does_not_exist": "result"},
        )
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert not any(event.get("event") == "step_started" for event in events)
    assert "result" not in ctx.globals.snapshot()


def test_atomic_variable_set_commits_all_values_together(tmp_path: Path):
    ctx = _ctx(tmp_path)
    committed = ctx.globals.set_many_atomic({"a": 1, "b": 2})
    assert committed == {"a": 1, "b": 2}
    assert ctx.globals.snapshot()["a"] == 1
    assert ctx.globals.snapshot()["b"] == 2


def test_detailed_invocation_extracts_descriptor_output_once(tmp_path: Path):
    from automation_harness.core.step_registry import step

    class Result:
        def __init__(self):
            self.reads = 0

        @property
        def value(self):
            self.reads += 1
            return self.reads

    @step("framework_test.descriptor_once", domain="framework_test", outputs={"value": "value"})
    def descriptor_once(ctx):
        return Result()

    ctx = _ctx(tmp_path)
    invocation = ctx.run_step_detailed(
        "framework_test.descriptor_once",
        bind_outputs={"value": "captured"},
    )
    assert invocation.outputs["value"] == 1
    assert invocation.result.reads == 1
    assert ctx.globals.get("captured") == 1


def test_step_catalog_records_implementation_digest():
    definition = default_step_registry().get("validation.equal")
    payload = definition.to_dict()
    assert len(payload["implementation_sha256"]) == 64
