from types import SimpleNamespace

from automation_harness.models.component import CapturedComponent, ComponentState
from automation_harness.recording.adapters.atspi import (
    AtspiRecordingAdapter,
    _event_coordinates,
    _is_recordable_target,
)


class _Driver:
    available = True

    def __init__(self, target):
        self.target = target
        self.points = []
        self.sources = []

    def capture_scoped_at_point(self, x, y):
        self.points.append((x, y))
        return self.target

    def capture_event_source(self, source, *, settle_delay=0.08):
        self.sources.append((source, settle_delay))
        return self.target


def _target():
    return CapturedComponent(
        name="Save", role="push button", description=None, accessible_id="save",
        application="Example", hierarchy=(), actions=("click",), bounds=(10, 20, 30, 40),
        state=ComponentState(True),
    )


def test_pointer_event_is_re_hit_tested_at_desktop_coordinates():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)
    emitted = []
    adapter._emit = emitted.append
    event = SimpleNamespace(type="mouse:button:1r", detail1=125, detail2=240, source=None)
    adapter._pointer_worker.start()
    adapter._pointer(event)
    adapter._pointer_worker.stop_and_drain()
    assert driver.points == [(125, 240)]
    assert emitted[0].target.name == "Save"
    assert emitted[0].coordinates == (125, 240)


def test_pointer_release_uses_semantic_target_resolved_from_matching_press():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)
    emitted = []
    adapter._emit = emitted.append

    adapter._pointer_worker.start()
    adapter._pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=object(),
    ))
    adapter._pointer(SimpleNamespace(
        type="mouse:button:1r", detail1=126, detail2=241, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert driver.sources == []
    assert driver.points == [(125, 240)]
    assert len(emitted) == 1
    assert emitted[0].target.name == "Save"
    assert emitted[0].phase == "released"
    assert emitted[0].coordinates == (126, 241)


def test_registry_pointer_callback_only_enqueues_resolution_work():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)
    event = SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=object(),
    )

    assert adapter._pointer_worker.accept_pointer("mouse:button:1p", (1, 2), 1.0) is False
    adapter._pointer_worker.start()
    adapter._pointer(event)

    assert driver.sources == []
    adapter._pointer_worker.stop_and_drain()
    assert driver.points == [(125, 240)]
    assert driver.sources == []
    assert adapter._pointer_worker.state == "stopped"
    assert adapter._pointer_worker.accept_pointer("mouse:button:1p", (1, 2), 2.0) is False


def test_pointer_press_coordinates_override_generic_shell_event_source():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)

    adapter._pointer_worker.start()
    adapter._pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=500, detail2=600, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert driver.points == [(500, 600)]
    assert driver.sources == []


def test_resolved_target_is_acknowledged_before_next_interaction():
    driver = _Driver(_target())
    events = []
    adapter = AtspiRecordingAdapter(
        driver,
        on_resolved=lambda target, duration: events.append(("highlight", target.name)),
        acknowledgement_seconds=0,
    )
    adapter._emit = lambda observation: events.append(("emit", observation.target.name))
    adapter._pointer_worker.start()
    adapter._pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=10, detail2=20, source=object(),
    ))
    adapter._pointer(SimpleNamespace(
        type="mouse:button:1r", detail1=10, detail2=20, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert events == [("highlight", "Save"), ("emit", "Save")]


def test_action_and_text_events_drain_against_last_resolved_target():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)
    emitted = []
    adapter._emit = emitted.append
    adapter._pointer_worker.start()
    adapter._pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=10, detail2=20, source=object(),
    ))
    adapter._pointer(SimpleNamespace(
        type="mouse:button:1r", detail1=10, detail2=20, source=object(),
    ))
    adapter._action(SimpleNamespace(
        type="object:state-changed:checked", detail1=True, source=object(),
    ))
    adapter._text(SimpleNamespace(
        type="object:text-changed:insert", any_data="updated", source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert [type(item).__name__ for item in emitted] == [
        "PointerInteraction", "StateChanged", "ActionFired", "TextChanged",
    ]
    assert all(item.target.name == "Save" for item in emitted)


def test_invalid_device_coordinates_are_not_used():
    assert _event_coordinates(SimpleNamespace(detail1=True, detail2=4)) is None
    assert _event_coordinates(SimpleNamespace(detail1=-1, detail2=4)) is None


def test_coordinate_capture_applies_the_same_semantic_filter_as_source_capture():
    passive = CapturedComponent(
        name="2", role="label", description=None, accessible_id=None,
        application="Calculator", hierarchy=("Calculator", "panel", "2"),
        actions=(), bounds=(10, 20, 30, 40), state=ComponentState(True),
    )
    adapter = AtspiRecordingAdapter(_Driver(passive))

    assert adapter._target(SimpleNamespace(source=object()), (12, 24)) is None
    assert not _is_recordable_target(passive)


def test_coordinate_capture_rejects_structural_panels_and_authoring_chrome():
    panel = CapturedComponent(
        name="content", role="panel", description=None, accessible_id="content",
        application="Example", hierarchy=(), actions=(), bounds=(1, 2, 3, 4),
        state=ComponentState(True),
    )
    harness = CapturedComponent(
        name="Save", role="push button", description=None, accessible_id="save",
        application="Automation Harness", hierarchy=(), actions=("click",),
        bounds=(1, 2, 3, 4), state=ComponentState(True),
    )

    assert not _is_recordable_target(panel)
    assert not _is_recordable_target(harness)


def test_source_events_use_driver_canonical_click_resolver():
    target = _target()
    driver = _Driver(target)
    adapter = AtspiRecordingAdapter(driver)
    source = object()
    assert adapter._target(SimpleNamespace(source=source)) is target
    assert driver.sources == [(source, 0.0)]
