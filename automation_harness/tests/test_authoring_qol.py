from automation_harness.authoring.app import AuthoringApp, _next_node_id
from automation_harness.core.component_repository import ComponentRepository
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


def test_authoring_stores_object_id_but_displays_logical_name():
    repository = ComponentRepository.from_document({"version": 2, "components": {
        "login.submit": {
            "object_type": "button",
            "actions": ["click"],
            "strategies": [{"type": "atspi", "name": "Submit", "role": "push button"}],
        },
    }})
    app = object.__new__(AuthoringApp)
    app.repository = repository
    definition = repository.get("login.submit")

    canonical = app._canonicalize_component_inputs({"component_id": "login.submit", "action": "click"})

    assert canonical["component_id"] == definition.object_id
    assert app._display_plan_inputs(canonical)["component_id"] == "login.submit"
