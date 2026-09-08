import json
from types import SimpleNamespace

from automation_harness.reporting.html_report import render_html_report


def test_html_report_renders_expected_and_actual_evidence(tmp_path):
    events = tmp_path / "events.jsonl"
    records = [
        {"event": "plan_step_started", "node_id": "assert-object", "step": "gui.object.exists.assert"},
        {
            "event": "assertion",
            "node_id": "assert-object",
            "assertion": "component_exists",
            "component_id": "toolbar.save",
            "passed": False,
            "message": "component did not resolve",
            "evidence": [
                {"role": "expected", "type": "component_state", "value": {"present": True}},
                {"role": "actual", "type": "component_state", "value": {"present": False}},
                {"role": "diagnostic", "type": "exception", "value": {"message": "not found"}},
            ],
        },
        {"event": "plan_step_failed", "node_id": "assert-object", "step": "gui.object.exists.assert", "error": "AssertionError"},
    ]
    events.write_text("".join(json.dumps(item) + "\n" for item in records), encoding="utf-8")
    plan = SimpleNamespace(
        name="Toolbar validation",
        steps=(SimpleNamespace(node_id="assert-object", step_id="gui.object.exists.assert", group="Toolbar"),),
    )
    result = SimpleNamespace(
        exit_code=1,
        backend="live-desktop",
        passed=0,
        failed=1,
        run_id="run-1",
        validation_errors=[],
    )

    report = tmp_path / "report.html"
    render_html_report(report, events, plan, result)
    rendered = report.read_text(encoding="utf-8")

    assert "Toolbar validation" in rendered
    assert "Expected" in rendered
    assert "Actual" in rendered
    assert "toolbar.save" in rendered
    assert "component did not resolve" in rendered
    assert '<details class="step-card failed" open>' in rendered
