import json
from types import SimpleNamespace

import pytest

from automation_harness.models.evidence import EvidenceItem
from automation_harness.steps import gui_steps
from automation_harness.utils.evidence import EvidenceRecorder


class _Context:
    def __init__(self, tmp_path, handle):
        self.run_dir = tmp_path
        self.evidence = EvidenceRecorder(tmp_path / "events.jsonl")
        self.execution_node_id = "assert-object"
        self._handle = handle

    def component(self, _component_id):
        return self._handle


class _Handle:
    def __init__(self, resolved=None, error=None, calls=None, strategies=None):
        self._resolved = resolved
        self._error = error
        self.calls = calls if calls is not None else []
        self.definition = SimpleNamespace(
            component_id="toolbar.save",
            strategies=tuple(strategies or ()),
        )

    def resolve(self):
        self.calls.append("resolve")
        if self._error is not None:
            raise self._error
        return self._resolved


class _Vision:
    def __init__(self, context, calls=None):
        self.context = context
        self.calls = calls

    def capture_region(self, _bounds, *, name="region"):
        if self.calls is not None:
            self.calls.append("capture")
        directory = self.context.run_dir / "screenshots"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (name + ".png")
        path.write_bytes(b"png")
        return path


def _events(tmp_path):
    return [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]


def test_evidence_item_requires_run_relative_artifact_paths():
    with pytest.raises(ValueError):
        EvidenceItem.artifact("actual", "image", "/tmp/outside.png")
    item = EvidenceItem.artifact("expected", "image", "vision/expected.png")
    assert item.to_dict() == {
        "role": "expected",
        "type": "image",
        "path": "vision/expected.png",
    }


def test_assert_exists_activates_window_before_refresh_and_capture(tmp_path, monkeypatch):
    calls = []
    resolved = SimpleNamespace(strategy="javafx", metadata={"bounds": [10, 20, 100, 30]})
    handle = _Handle(resolved=resolved, calls=calls)
    context = _Context(tmp_path, handle)

    def activate(_ctx, _handle):
        calls.append("activate_window")
        return {"strategy": "javafx", "details": {"operation": "activate_window"}}

    monkeypatch.setattr(gui_steps, "_activate_owning_window", activate)
    monkeypatch.setattr(gui_steps, "VisionDriver", lambda ctx: _Vision(ctx, calls))

    result = gui_steps.gui_object_exists_assert.__wrapped__(context, "toolbar.save")

    assert calls == ["resolve", "activate_window", "resolve", "capture"]
    assert result["window_activation"]["strategy"] == "javafx"


def test_assert_exists_records_expected_state_and_actual_component_image(tmp_path, monkeypatch):
    resolved = SimpleNamespace(strategy="javafx", metadata={"bounds": [10, 20, 100, 30]})
    context = _Context(tmp_path, _Handle(resolved=resolved))
    monkeypatch.setattr(
        gui_steps,
        "_activate_owning_window",
        lambda _ctx, _handle: {"strategy": "javafx", "details": {"operation": "activate_window"}},
    )
    monkeypatch.setattr(gui_steps, "VisionDriver", _Vision)

    result = gui_steps.gui_object_exists_assert.__wrapped__(context, "toolbar.save")

    assert result["present"] is True
    assertion = [event for event in _events(tmp_path) if event["event"] == "assertion"][0]
    assert assertion["assertion"] == "component_exists"
    assert assertion["node_id"] == "assert-object"
    assert assertion["passed"] is True
    assert assertion["window_activation"]["strategy"] == "javafx"
    assert {item["role"] for item in assertion["evidence"]} == {"expected", "actual"}
    assert any(
        item["role"] == "expected"
        and item["type"] == "component_state"
        and item["value"] == {"present": True}
        for item in assertion["evidence"]
    )
    assert any(
        item["role"] == "actual"
        and item["type"] == "image"
        and item["path"].startswith("screenshots/")
        for item in assertion["evidence"]
    )


def test_assert_exists_window_activation_failure_is_evidence_failure(tmp_path, monkeypatch):
    resolved = SimpleNamespace(strategy="javafx", metadata={"bounds": [10, 20, 100, 30]})
    context = _Context(tmp_path, _Handle(resolved=resolved))

    def fail_activation(_ctx, _handle):
        raise RuntimeError("window did not come forward")

    monkeypatch.setattr(gui_steps, "_activate_owning_window", fail_activation)

    with pytest.raises(AssertionError, match="owning window could not be activated"):
        gui_steps.gui_object_exists_assert.__wrapped__(context, "toolbar.save")

    assertion = [event for event in _events(tmp_path) if event["event"] == "assertion"][0]
    assert assertion["passed"] is False
    assert assertion["actual"] is True
    diagnostic = [item for item in assertion["evidence"] if item["role"] == "diagnostic"][0]
    assert diagnostic["type"] == "exception"


def test_window_activation_uses_native_javafx_strategy(tmp_path, monkeypatch):
    strategy = SimpleNamespace(
        type="javafx",
        options={"identification": {"mandatory": {"id": "fileMenu"}}},
    )
    handle = _Handle(strategies=(strategy,))
    context = _Context(tmp_path, handle)
    calls = []

    class _JavaFx:
        def __init__(self, _context):
            pass

        def activate_window(self, *, identification=None):
            calls.append(identification)
            return {"operation": "activate_window", "window": "Main"}

    monkeypatch.setattr(gui_steps, "JavaFxBridgeDriver", _JavaFx)

    result = gui_steps._activate_owning_window(context, handle)

    assert result["strategy"] == "javafx"
    assert calls == [{"mandatory": {"id": "fileMenu"}}]


def test_assert_exists_failure_records_expected_and_actual_evidence(tmp_path):
    context = _Context(tmp_path, _Handle(error=LookupError("not found")))

    with pytest.raises(AssertionError, match="did not resolve"):
        gui_steps.gui_object_exists_assert.__wrapped__(context, "toolbar.save")

    assertion = [event for event in _events(tmp_path) if event["event"] == "assertion"][0]
    assert assertion["passed"] is False
    by_role = {}
    for item in assertion["evidence"]:
        by_role.setdefault(item["role"], []).append(item)
    assert by_role["expected"][0]["value"] == {"present": True}
    assert by_role["actual"][0]["value"] == {"present": False}
    assert by_role["diagnostic"][0]["type"] == "exception"
