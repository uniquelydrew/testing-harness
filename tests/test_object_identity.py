"""Semantic identity is durable; runtime process/window bindings are not."""
import pytest

from automation_harness.core.object_identity import (
    ApplicationIdentity, WindowIdentity, ComponentIdentity,
    RuntimeBinding, assess_runtime_ownership,
)


def test_window_identity_survives_application_restart():
    application = ApplicationIdentity(application_id="ersa")
    window = WindowIdentity(application, role="frame", title="Camera Settings")
    component = ComponentIdentity(window, role="combo box", name="Primary Camera")
    first = RuntimeBinding(process_id=123, native_window_id=1001, backend="atspi")
    second = RuntimeBinding(process_id=456, native_window_id=2002, backend="atspi")
    assert first != second
    assert component.window == window
    assert component.name == "Primary Camera"


def test_ownership_distinguishes_missing_evidence_from_mismatch():
    assert assess_runtime_ownership(123, 123) == "confirmed"
    assert assess_runtime_ownership(123, 456) == "mismatch"
    assert assess_runtime_ownership(123, None) == "unverified"


def test_semantic_identity_requires_discriminator():
    with pytest.raises(ValueError):
        WindowIdentity(ApplicationIdentity(executable="ersa"), role="frame")
    with pytest.raises(ValueError):
        ComponentIdentity(
            WindowIdentity(ApplicationIdentity(executable="ersa"), role="frame", title="Main"),
            role="button",
        )


def test_runtime_binding_rejects_invalid_ids():
    with pytest.raises(ValueError):
        RuntimeBinding(process_id=0)
    with pytest.raises(ValueError):
        RuntimeBinding(native_window_id=-1)
