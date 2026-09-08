from automation_harness.backends.protected import ProtectedBackend
from automation_harness.backends.reference import ReferenceBackend


def test_headless_reference_has_no_gui_capabilities():
    backend = ReferenceBackend(gui=False)
    assert "gui" not in backend.capabilities
    assert "screen-capture" not in backend.capabilities


def test_reference_backend_headless_starts_on_supported_local_endpoint(tmp_path):
    """The reference backend must be runnable in Windows CI as well as Unix."""
    backend = ReferenceBackend(gui=False)
    run_dir = tmp_path / "run"
    (run_dir / "logs").mkdir(parents=True)
    try:
        environment = backend.start(run_dir=run_dir)
        assert environment["AUTOMATION_HARNESS_SOCKET"]
        assert backend.health_check().healthy
    finally:
        backend.stop()


def test_protected_backend_is_explicitly_disabled():
    issues = ProtectedBackend().preflight_issues()
    assert issues
    assert "intentionally disabled" in issues[0]
