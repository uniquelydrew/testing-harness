from automation_harness.backends.protected import ProtectedBackend
from automation_harness.backends import reference
from automation_harness.backends.reference import ReferenceBackend


def test_headless_reference_has_no_gui_capabilities():
    backend = ReferenceBackend(gui=False)
    assert "gui" not in backend.capabilities
    assert "screen-capture" not in backend.capabilities


def test_gui_reference_without_pillow_still_supports_semantic_component_tests(monkeypatch):
    monkeypatch.setattr(reference, "_pillow_available", lambda: False)
    backend = ReferenceBackend(gui=True, display_mode="auto")
    assert "gui" in backend.capabilities
    assert "components" in backend.capabilities
    assert "screen-capture" not in backend.capabilities
    assert not any("Pillow" in issue for issue in backend.preflight_issues())


def test_protected_backend_is_explicitly_disabled():
    issues = ProtectedBackend().preflight_issues()
    assert issues
    assert "intentionally disabled" in issues[0]
