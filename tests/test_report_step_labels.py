"""Regression coverage for authored step labels in HTML reports."""
from automation_harness.models.plan import StepCall
from automation_harness.reporting.html_report import _step_display_name


def test_custom_step_name_takes_precedence():
    step = StepCall(node_id="node-1", step_id="click", name="Open Camera Menu",
                    description="A lower-priority description")
    assert _step_display_name(step) == "Open Camera Menu"


def test_description_precedes_internal_identifier():
    step = StepCall(node_id="node-2", step_id="select.camera", description="Select Camera")
    assert _step_display_name(step) == "Select Camera"


def test_step_id_is_readable_fallback():
    step = StepCall(node_id="node-3", step_id="select_camera")
    assert _step_display_name(step) == "select camera"
