from types import SimpleNamespace

from automation_harness.models.component import CapturedComponent, ComponentState
from automation_harness.recording.adapters.atspi import AtspiRecordingAdapter, _event_coordinates


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
