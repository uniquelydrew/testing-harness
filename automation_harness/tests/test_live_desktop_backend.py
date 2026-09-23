from pathlib import Path

from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.component_handle import ComponentHandle
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType


def test_live_backend_represents_desktop_session_only(monkeypatch, tmp_path: Path):
    from automation_harness.backends import live_desktop

    monkeypatch.setattr(live_desktop.platform, "system", lambda: "Linux")
    monkeypatch.setattr(live_desktop.AtspiDriver, "available", property(lambda self: True))
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/test-bus")

    backend = LiveDesktopBackend()
    environment = backend.start(run_dir=tmp_path)

    assert backend.name == "live-desktop"
    assert environment["AUTOMATION_HARNESS_BACKEND"] == "live-desktop"
    assert "AUTOMATION_HARNESS_ATTACHED_APPLICATION" not in environment
    assert backend.health_check().details["desktop_session"] == "current"
    backend.stop()


def test_object_application_lineage_remains_object_local():
    identification = {
        "mandatory": {"name": "Show All"},
        "assistive": {"application": "Application A", "window": "Main"},
    }
    definition = ComponentDefinition(
        "show-all",
        object_type=ObjectType.BUTTON,
        actions=frozenset({"click"}),
        strategies=(ComponentStrategy("atspi", {"identification": identification}),),
    )

    # Resolution receives the repository strategy unchanged. Application and
    # window are object-local assistive criteria, not a process-wide execution
    # target injected by the harness.
    import automation_harness.core.component_handle as component_handle
    from types import SimpleNamespace

    observed = []
    original = component_handle.resolve_live_capture
    component_handle.resolve_live_capture = lambda strategy, options, context: (
        observed.append((strategy, options))
        or SimpleNamespace(to_dict=lambda: {}, bounds=None)
    )
    try:
        ComponentHandle(SimpleNamespace(), definition)._resolve_strategy(
            "atspi", definition.strategies[0].options,
        )
    finally:
        component_handle.resolve_live_capture = original

    assert observed == [("atspi", {"identification": identification})]


def test_objects_from_multiple_applications_need_no_execution_target():
    first = {"mandatory": {"name": "Save"}, "assistive": {"application": "Application A"}}
    second = {"mandatory": {"name": "Status"}, "assistive": {"application": "Application B"}}

    definitions = (
        ComponentDefinition(
            "save", object_type=ObjectType.BUTTON,
            strategies=(ComponentStrategy("atspi", {"identification": first}),),
        ),
        ComponentDefinition(
            "status", object_type=ObjectType.BUTTON,
            strategies=(ComponentStrategy("atspi", {"identification": second}),),
        ),
    )
    assert [item.strategies[0].options["identification"] for item in definitions] == [
        first, second,
    ]
