from pathlib import Path

from automation_harness.backends.attached_desktop import AttachedDesktopBackend
from automation_harness.core.component_handle import ComponentHandle, ComponentResolutionError
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType


def test_attached_backend_never_owns_application_lifecycle(monkeypatch, tmp_path: Path):
    from automation_harness.backends import attached_desktop
    monkeypatch.setattr(attached_desktop.JavaAccessibilityDriver, "application_present", classmethod(lambda cls, name=None: name == "ERSA"))
    monkeypatch.setattr(attached_desktop.JavaAccessibilityDriver, "available", property(lambda self: True))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/test-bus")
    backend = AttachedDesktopBackend({"expected_application": "ERSA"})
    environment = backend.start(run_dir=tmp_path)
    assert environment["AUTOMATION_HARNESS_ATTACHED_APPLICATION"] == "ERSA"
    assert backend.health_check().healthy
    backend.stop()
    assert backend.health_check().healthy


def test_attached_target_is_forced_into_mandatory_object_identity():
    class Context:
        target_application = "ERSA"

    definition = ComponentDefinition(
        "show-all", object_type=ObjectType.BUTTON, actions=frozenset({"click"}),
        strategies=(ComponentStrategy("atspi", {"identification": {
            "mandatory": {"name": "Show All"},
            "assistive": {"application": "ERSA", "window": "Main"},
        }}),),
    )
    scoped = ComponentHandle(Context(), definition)._scoped_identification(definition.strategies[0].options["identification"])
    assert scoped["mandatory"]["application"] == "ERSA"
    assert "application" not in scoped["assistive"]


def test_cross_application_object_is_rejected_before_resolution():
    class Context:
        target_application = "ERSA"

    definition = ComponentDefinition("foreign", object_type=ObjectType.BUTTON)
    handle = ComponentHandle(Context(), definition)
    try:
        handle._scoped_identification({"mandatory": {"name": "Save"}, "assistive": {"application": "Other App"}})
    except ComponentResolutionError as exc:
        assert "Other App" in str(exc)
        assert "ERSA" in str(exc)
    else:
        raise AssertionError("cross-application identity was accepted")
