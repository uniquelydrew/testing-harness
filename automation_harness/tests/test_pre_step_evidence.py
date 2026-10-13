from pathlib import Path
from types import SimpleNamespace

from automation_harness.runner.pre_step_evidence import collect_pre_step_evidence


class _Resolved:
    metadata = {"bounds": (2, 3, 4, 5)}


class _Handle:
    def resolve(self):
        return _Resolved()


class _Context:
    def __init__(self, root):
        self.run_dir = root
        self.components = SimpleNamespace(components={"button": object()})

    def component(self, component_id):
        return _Handle()


def test_no_component_references_do_not_require_screen(tmp_path):
    summary = collect_pre_step_evidence(_Context(tmp_path), "step-1", {"text": "button"})
    assert summary.evidence_status == "not_applicable"


def test_missing_screenshot_backend_produces_evidence_warning_not_exception(tmp_path, monkeypatch):
    from PIL import ImageGrab
    def unavailable():
        raise OSError("no display")
    monkeypatch.setattr(ImageGrab, "grab", unavailable)
    summary = collect_pre_step_evidence(_Context(tmp_path), "step-1", {"component_id": "button"})
    assert summary.evidence_status == "unavailable"
    assert "no display" in summary.components[0].diagnostic


def test_pre_step_capture_uses_resolved_bounds(tmp_path, monkeypatch):
    from PIL import Image, ImageGrab
    monkeypatch.setattr(ImageGrab, "grab", lambda: Image.new("RGB", (20, 20)))
    summary = collect_pre_step_evidence(_Context(tmp_path), "step-1", {"component_id": "button"})
    assert summary.evidence_status == "complete"
    path = tmp_path / summary.components[0].image_path
    assert path.exists()
    assert Image.open(path).size == (20, 20)  # 16px context margin clipped to desktop


def test_capture_margin_uses_component_context(tmp_path, monkeypatch):
    from PIL import Image, ImageGrab
    monkeypatch.setattr(ImageGrab, "grab", lambda: Image.new("RGB", (100, 100)))
    summary = collect_pre_step_evidence(_Context(tmp_path), "step-2", {"component_id": "button"}, margin=10)
    assert Image.open(tmp_path / summary.components[0].image_path).size == (16, 18)
