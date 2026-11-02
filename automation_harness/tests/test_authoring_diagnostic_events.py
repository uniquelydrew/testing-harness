"""Authoring event logs are opt-in, bounded and nonfatal."""
import hashlib
import json

from automation_harness.authoring.diagnostic_events import artifact_fingerprint, emit
from automation_harness.authoring.plan_open import inspect_plan_open
from automation_harness.core.test_plan import save_plan
from automation_harness.models.plan import TestPlan


def test_fingerprint_is_stable_without_reading_private_body(tmp_path):
    path = tmp_path / "plan.ahplan"
    path.write_bytes(b"private-content")
    info = artifact_fingerprint(path)
    assert info == {"exists": True, "bytes": len(b"private-content"),
                    "sha256": hashlib.sha256(b"private-content").hexdigest()}


def test_diagnostic_event_is_metadata_only_and_opt_in(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_CODEX_SESSION_DIR", raising=False)
    emit("test", secret="DO_NOT_LOG")
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setenv("AUTOMATION_HARNESS_CODEX_SESSION_DIR", str(tmp_path))
    emit("test", legacy_count=3, secret="DO_NOT_LOG", error_type="ValueError")
    event = json.loads((tmp_path / "authoring-events.jsonl").read_text().strip())
    assert event["event"] == "test"
    assert event["fields"] == {"legacy_count": 3, "error_type": "ValueError"}
    assert "DO_NOT_LOG" not in json.dumps(event)


def test_plan_open_diagnostics_are_nonmutating_and_count_only(tmp_path, monkeypatch):
    path = tmp_path / "plan.ahplan"
    save_plan(TestPlan(name="plan"), path)
    original = path.read_bytes()
    monkeypatch.setenv("AUTOMATION_HARNESS_CODEX_SESSION_DIR", str(tmp_path))
    inspect_plan_open(path)
    rows = [json.loads(line) for line in
            (tmp_path / "authoring-events.jsonl").read_text().splitlines()]
    assert [row["event"] for row in rows] == [
        "plan_open_requested", "legacy_detection_completed",
    ]
    assert rows[-1]["fields"]["legacy_count"] == 0
    assert path.read_bytes() == original


def test_events_never_block_authored_operations(monkeypatch, tmp_path):
    nonexistent = tmp_path / "not-a-session"
    monkeypatch.setenv("AUTOMATION_HARNESS_CODEX_SESSION_DIR", str(nonexistent))
    emit("anything", legacy_count=1)
    assert not nonexistent.exists()
