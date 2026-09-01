from pathlib import Path

from automation_harness.backends.attached_desktop import AttachedDesktopBackend
from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_context import TestContext
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType
from automation_harness.utils.evidence import EvidenceRecorder


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


def test_legacy_target_value_is_ignored_by_test_context(tmp_path: Path):
    context = TestContext(
        backend="live-desktop",
        run_dir=tmp_path,
        evidence=EvidenceRecorder(tmp_path / "events.jsonl"),
        components=ComponentRepository({}),
        capabilities=frozenset(),
        steps=default_step_registry(),
        target_application="ERSA",
    )
    assert context.target_application is None


def test_object_application_lineage_is_not_promoted_to_mandatory_target_scope():
    class Context:
        target_application = None

    identification = {
        "mandatory": {"name": "Show All"},
        "assistive": {"application": "ERSA", "window": "Main"},
    }
    definition = ComponentDefinition(
        "show-all", object_type=ObjectType.BUTTON, actions=frozenset({"click"}),
        strategies=(ComponentStrategy("atspi", {"identification": identification}),),
    )
    scoped = ComponentHandle(Context(), definition)._scoped_identification(identification)
    assert scoped == identification
    assert "application" not in scoped["mandatory"]
    assert scoped["assistive"]["application"] == "ERSA"


def test_objects_from_multiple_applications_are_valid_without_test_target():
    class Context:
        target_application = None

    definition = ComponentDefinition("foreign", object_type=ObjectType.BUTTON)
    handle = ComponentHandle(Context(), definition)
    identification = {
        "mandatory": {"name": "Save"},
        "assistive": {"application": "Other App"},
    }
    assert handle._scoped_identification(identification) == identification
