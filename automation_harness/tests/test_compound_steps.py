from __future__ import annotations

import pytest

from automation_harness.core.compiler import compile_test
from automation_harness.core.compound_steps import CompoundStepError, CompoundStepRepository, expand_compound_steps
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import default_step_registry
from automation_harness.models.plan import StepCall, TestPlan


def _repository() -> CompoundStepRepository:
    return CompoundStepRepository.from_document({
        "version": 1,
        "steps": {
            "validation.round_trip": {
                "inputs": ["value"],
                "outputs": {"result": "captured"},
                "calls": [
                    {
                        "id": "capture",
                        "step": "track.create_moving",
                        "inputs": {"track_id": {"$input": "value"}},
                        "outputs": {"track_id": "captured"},
                    },
                    {
                        "id": "verify",
                        "step": "validation.equal",
                        "inputs": {
                            "name": "round-trip",
                            "actual": {"$local": "captured"},
                            "expected": {"$local": "captured"},
                        },
                    },
                ],
            },
        },
    })


def test_compound_step_expands_with_namespaced_nodes_and_output_binding():
    plan = TestPlan(
        name="compound",
        steps=(StepCall(
            node_id="round-trip",
            step_id="validation.round_trip",
            inputs={"value": "alpha"},
            outputs={"result": "final_value"},
        ),),
    )
    expanded, used = expand_compound_steps(plan, _repository())
    assert used == ("validation.round_trip",)
    assert [call.node_id for call in expanded.steps] == ["round-trip/capture", "round-trip/verify"]
    assert expanded.steps[0].outputs == {"track_id": "final_value"}
    assert expanded.steps[1].depends_on == ("round-trip/capture",)


def test_dependency_on_compound_invocation_targets_its_terminal_instruction():
    plan = TestPlan(
        name="compound-dependency",
        steps=(
            StepCall(
                node_id="round-trip",
                step_id="validation.round_trip",
                inputs={"value": "alpha"},
                outputs={"result": "final_value"},
            ),
            StepCall(
                node_id="after",
                step_id="validation.equal",
                inputs={"name": "after", "actual": 1, "expected": 1},
                depends_on=("round-trip",),
            ),
        ),
    )
    expanded, _ = expand_compound_steps(plan, _repository())
    assert expanded.steps[-1].depends_on == ("round-trip/verify",)


def test_compiler_embeds_compound_definition_but_emits_primitive_instructions():
    plan = TestPlan(
        name="compound",
        steps=(StepCall(
            node_id="round-trip",
            step_id="validation.round_trip",
            inputs={"value": "alpha"},
            outputs={"result": "final_value"},
        ),),
    )
    artifact = compile_test(
        plan,
        default_step_registry(),
        ComponentRepository({}),
        compound_steps=_repository(),
    )
    assert "validation.round_trip" in artifact.document["dependencies"]["compound_steps"]
    assert [item["step"] for item in artifact.document["instructions"]] == [
        "track.create_moving",
        "validation.equal",
    ]


def test_compound_cycle_is_rejected():
    repository = CompoundStepRepository.from_document({
        "version": 1,
        "steps": {
            "a": {"inputs": [], "outputs": {}, "calls": [{"id": "b", "step": "b"}]},
            "b": {"inputs": [], "outputs": {}, "calls": [{"id": "a", "step": "a"}]},
        },
    })
    with pytest.raises(CompoundStepError, match="dependency cycle"):
        expand_compound_steps(TestPlan(name="cycle", steps=(StepCall(node_id="a", step_id="a"),)), repository)


def test_explicit_compound_completion_emits_terminal_barrier():
    repository = _repository()
    definition = repository.get("validation.round_trip")
    from dataclasses import replace
    repository = CompoundStepRepository({
        definition.step_id: replace(
            definition,
            completion={"mode": "explicit", "condition": {"variable": "final_value", "equals": "alpha"}},
        ),
    })
    plan = TestPlan(
        name="compound-completion",
        steps=(StepCall(
            node_id="round-trip",
            step_id="validation.round_trip",
            inputs={"value": "alpha"},
            outputs={"result": "final_value"},
        ),),
    )
    expanded, _ = expand_compound_steps(plan, repository)
    assert expanded.steps[-1].step_id == "framework.completion.barrier"
    assert expanded.steps[-1].depends_on == ("round-trip/verify",)
