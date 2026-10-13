from automation_harness.models.step_evidence import (
    ComponentEvidence, StepEvidenceSummary, display_status, referenced_components,
)


def test_warn_does_not_override_execution_failure():
    assert display_status("passed", "partial") == "WARN"
    assert display_status("passed", "unavailable") == "WARN"
    assert display_status("passed", "not_applicable") == "PASS"
    assert display_status("failed", "partial") == "FAIL"
    assert display_status("skipped", "unavailable") == "SKIP"


def test_evidence_coverage_and_serialization():
    summary = StepEvidenceSummary("step-1", "attempt-1", (
        ComponentEvidence("button", "captured", "evidence/button.png"),
        ComponentEvidence("window", "unavailable", diagnostic="occluded"),
    ))
    data = summary.to_dict()
    assert data["evidence_status"] == "partial"
    assert data["captured_components"] == 1
    assert data["expected_components"] == 2
    assert data["capture_phase"] == "pre_execution"


def test_reference_discovery_ignores_untyped_strings():
    assert referenced_components({
        "text": "button", "component_id": "button",
        "nested": [{"object_id": "window"}, {"description": "window"}],
    }, {"button", "window"}) == ("button", "window")
