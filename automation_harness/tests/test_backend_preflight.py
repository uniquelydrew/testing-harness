from automation_harness.backends.protected import ProtectedBackend
from automation_harness.backends.reference import ReferenceBackend


def test_headless_reference_has_no_gui_capabilities():
    backend = ReferenceBackend(gui=False)
    assert "gui" not in backend.capabilities
    assert "screen-capture" not in backend.capabilities


def test_protected_backend_is_explicitly_disabled():
    issues = ProtectedBackend().preflight_issues()
    assert issues
    assert "intentionally disabled" in issues[0]
