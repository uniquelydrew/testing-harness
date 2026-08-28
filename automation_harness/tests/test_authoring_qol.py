from automation_harness.authoring.app import _next_node_id
from automation_harness.models.plan import StepCall


def test_next_node_id_fills_gap_without_colliding():
    steps = (
        StepCall(node_id="step-001", step_id="validation.equal"),
        StepCall(node_id="step-003", step_id="validation.equal"),
        StepCall(node_id="custom-node", step_id="validation.equal"),
    )
    assert _next_node_id(steps) == "step-002"


def test_next_node_id_advances_past_contiguous_ids():
    steps = tuple(
        StepCall(node_id="step-%03d" % index, step_id="validation.equal")
        for index in range(1, 5)
    )
    assert _next_node_id(steps) == "step-005"
