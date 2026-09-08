from __future__ import annotations

import json

import pytest

from automation_harness.core.compiler import COMPILED_TEST_FORMAT, CompiledTest, compile_test
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import default_step_registry
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan


def _components() -> ComponentRepository:
    return ComponentRepository.from_document({
        "version": 2,
        "components": {
            "dialog.ok": {
                "object_type": "button",
                "actions": ["activate"],
                "strategies": [{
                    "type": "atspi",
                    "identification": {"mandatory": {"name": "OK", "role": "push button"}},
                }],
                "scope": {"parent": {"from": "execution.active_window"}},
                "action_completion": {
                    "click": {"effects": [{"object": "dialog.ok", "transition": "becomes-absent"}]},
                },
            },
        },
    })


def test_compilation_is_deterministic():
    plan = TestPlan(
        name="deterministic",
        steps=(
            StepCall(
                node_id="check",
                step_id="validation.equal",
                inputs={"name": "x", "actual": 1, "expected": 1},
            ),
        ),
    )
    first = compile_test(plan, default_step_registry(), ComponentRepository({}))
    second = compile_test(plan, default_step_registry(), ComponentRepository({}))
    assert first.to_json() == second.to_json()
    assert first.document["format"] == COMPILED_TEST_FORMAT
    assert len(first.digest) == 64


def test_compiled_artifact_verifies_integrity_and_rebuilds_runtime_plan():
    plan = TestPlan(
        name="verified",
        variables={"expected": 1},
        steps=(
            StepCall(
                node_id="check",
                step_id="validation.equal",
                inputs={
                    "name": "x",
                    "actual": PlanVariableRef("expected"),
                    "expected": 1,
                },
                completion={"mode": "dispatch-only"},
                scope={"window": "primary"},
            ),
        ),
    )
    compiled = compile_test(plan, default_step_registry(), ComponentRepository({}))
    loaded = CompiledTest.from_document(json.loads(compiled.to_json()))
    runtime = loaded.runtime_plan()

    assert runtime.name == plan.name
    assert runtime.variables == plan.variables
    assert runtime.steps == plan.steps
    assert loaded.validate_runtime(default_step_registry()) == []

    corrupted = json.loads(compiled.to_json())
    corrupted["instructions"][0]["inputs"]["expected"] = 2
    with pytest.raises(ValueError, match="digest mismatch"):
        CompiledTest.from_document(corrupted)


def test_compiler_tracks_variable_producers_and_consumers():
    plan = TestPlan(
        name="dataflow",
        steps=(
            StepCall(
                node_id="produce",
                step_id="track.create_moving",
                inputs={"track_id": "alpha"},
                outputs={"track_id": "created_track"},
            ),
            StepCall(
                node_id="consume",
                step_id="validation.equal",
                inputs={
                    "name": "track",
                    "actual": PlanVariableRef("created_track"),
                    "expected": "alpha",
                },
            ),
        ),
    )
    artifact = compile_test(plan, default_step_registry(), ComponentRepository({}))
    variables = artifact.document["variables"]

    assert variables["producers"]["created_track"]["invocation_id"] == "produce"
    assert variables["consumers"]["created_track"][0]["invocation_id"] == "consume"


def test_compiler_embeds_component_scope_completion_and_identity():
    components = _components()
    definition = components.get("dialog.ok")
    plan = TestPlan(
        name="component-contracts",
        steps=(
            StepCall(
                node_id="resolve",
                step_id="navigation.component.resolve",
                inputs={"component_id": "dialog.ok"},
                completion={"mode": "dispatch-only"},
            ),
        ),
    )

    artifact = compile_test(plan, default_step_registry(), components)
    embedded = artifact.document["dependencies"]["components"]["dialog.ok"]

    assert embedded["object_id"] == definition.object_id
    assert embedded["scope"] == definition.scope
    assert embedded["action_completion"] == definition.action_completion
    assert artifact.component_repository().get(definition.object_id).component_id == "dialog.ok"


def test_compiler_embeds_objects_referenced_only_by_completion_contracts():
    components = _components()
    plan = TestPlan(
        name="completion-dependency",
        steps=(
            StepCall(
                node_id="check",
                step_id="validation.equal",
                inputs={"name": "x", "actual": 1, "expected": 1},
                completion={
                    "mode": "explicit",
                    "condition": {"object": "dialog.ok", "state": "visible", "equals": True},
                },
            ),
        ),
    )

    artifact = compile_test(plan, default_step_registry(), components)

    assert "dialog.ok" in artifact.document["dependencies"]["components"]
    assert artifact.component_repository().get("dialog.ok").object_id == components.get("dialog.ok").object_id
