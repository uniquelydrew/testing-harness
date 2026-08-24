from __future__ import annotations

import json
from pathlib import Path

import pytest

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import StepOutputError, default_step_registry, step
from automation_harness.core.test_context import TestContext
from automation_harness.core.variables import VariableNotFoundError, VariableStore, VariableTypeError
from automation_harness.utils.evidence import EvidenceRecorder


@step(
    "framework_test.make_record",
    domain="framework_test",
    outputs={"record": "$", "identifier": "id", "nested_value": "nested.value"},
)
def _make_record(ctx: TestContext, identifier: str, *, amount: int = 1) -> dict:
    return {"id": identifier, "amount": amount, "nested": {"value": amount * 2}}


@step("framework_test.consume", domain="framework_test", outputs={"value": "$"})
def _consume(ctx: TestContext, identifier: str, amount: int) -> str:
    return f"{identifier}:{amount}"


def _context(tmp_path: Path) -> TestContext:
    evidence = EvidenceRecorder(tmp_path / "events.jsonl")
    return TestContext(
        backend="reference",
        run_dir=tmp_path,
        evidence=evidence,
        components=ComponentRepository({}),
        capabilities=frozenset(),
        steps=default_step_registry(),
        globals=VariableStore(evidence, {"history": [], "session": {"phase": "initial"}}),
    )


def test_step_catalog_exposes_input_and_output_contracts():
    definition = default_step_registry().get("framework_test.make_record")
    assert [item.name for item in definition.inputs] == ["identifier", "amount"]
    assert definition.inputs[0].required is True
    assert definition.inputs[1].required is False
    assert definition.inputs[1].default == 1
    assert {item.name: item.selector for item in definition.outputs} == {
        "record": "$",
        "identifier": "id",
        "nested_value": "nested.value",
    }


def test_step_output_can_bind_global_and_feed_later_step_input(tmp_path: Path):
    ctx = _context(tmp_path)
    ctx.run_step(
        "framework_test.make_record",
        "alpha",
        amount=7,
        bind_outputs={"identifier": "current_id", "nested_value": "derived_amount", "record": "last_record"},
    )

    assert ctx.globals is not None
    result = ctx.run_step(
        "framework_test.consume",
        ctx.ref("current_id"),
        ctx.ref("derived_amount"),
        bind_outputs={"value": "consumed"},
    )
    assert result == "alpha:14"
    assert ctx.globals.get("consumed") == "alpha:14"
    assert ctx.globals.get("last_record.id") == "alpha"

    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert any(event["event"] == "step_output_bound" and event["variable"] == "current_id" for event in events)
    assert any(event["event"] == "variable_resolved" and event["variable"] == "derived_amount" for event in events)


def test_globals_support_initialize_set_update_append_extend_and_nested_refs(tmp_path: Path):
    ctx = _context(tmp_path)
    assert ctx.globals is not None

    ctx.globals.set("counter", 1)
    ctx.globals.update("session", {"track": "alpha", "phase": "running"})
    ctx.globals.append("history", {"track": ctx.ref("session.track"), "state": "created"})
    ctx.globals.extend("history", [{"state": "followed"}, {"state": "verified"}])

    assert ctx.globals.get("counter") == 1
    assert ctx.globals.get("session.phase") == "running"
    assert ctx.globals.get("history.0.track") == "alpha"
    assert ctx.globals.get("history.2.state") == "verified"


def test_globals_reject_invalid_mutation_types_and_missing_refs(tmp_path: Path):
    ctx = _context(tmp_path)
    assert ctx.globals is not None
    ctx.globals.set("scalar", 4)
    with pytest.raises(VariableTypeError):
        ctx.globals.append("scalar", 5)
    with pytest.raises(VariableTypeError):
        ctx.globals.update("scalar", {"x": 1})
    with pytest.raises(VariableNotFoundError):
        ctx.globals.get("missing.value")


def test_binding_rejects_undeclared_step_output(tmp_path: Path):
    ctx = _context(tmp_path)
    with pytest.raises(StepOutputError, match="does not declare output"):
        ctx.run_step(
            "framework_test.make_record",
            "alpha",
            bind_outputs={"identifer": "current_id"},
        )
