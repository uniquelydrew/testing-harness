from types import SimpleNamespace
from unittest.mock import patch

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

    def capture_scoped_at_point(self, x, y):
        self.points.append((x, y))
        return self.target


def _target():
    return CapturedComponent(
        name="Save", role="push button", description=None, accessible_id="save",
        application="Example", hierarchy=(), actions=("click",), bounds=(10, 20, 30, 40),
        state=ComponentState(True),
    )


def test_pointer_event_is_re_hit_tested_at_desktop_coordinates():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver)
    emitted = []
    adapter._emit = emitted.append
    event = SimpleNamespace(type="mouse:button:1r", detail1=125, detail2=240, source=None)
    adapter._pointer(event)
    assert driver.points == [(125, 240)]
    assert emitted[0].target.name == "Save"
    assert emitted[0].coordinates == (125, 240)


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


def test_source_events_use_the_same_semantic_resolver_as_capture():
    target = _target()
    driver = _Driver(target)
    adapter = AtspiRecordingAdapter(driver)
    desktop = object()
    adapter._pyatspi = SimpleNamespace(
        Registry=SimpleNamespace(getDesktop=lambda _index: desktop),
    )
    source = object()

    with patch(
        "automation_harness.recording.adapters.atspi._capture_semantic_accessible",
        return_value=target,
    ) as resolve:
        assert adapter._target(SimpleNamespace(source=source)) is target

    resolve.assert_called_once_with(source, desktop, adapter._pyatspi)
