from dataclasses import replace
import threading
import time
from types import SimpleNamespace

from automation_harness.models.component import CapturedComponent, ComponentState
from automation_harness.drivers.atspi_driver import (
    AtspiDriver,
    AtspiExcludedClickSource,
    _deepest_at_point,
)
from automation_harness.recording.adapters.atspi import (
    AtspiRecordingAdapter,
    _event_coordinates,
    _is_recordable_target,
)


class _Driver:
    available = True

    def __init__(self, target, source_target=None):
        self.target = target
        self.source_target = source_target or target
        self.points = []
        self.sources = []
        self.point_snapshots = []
        self.source_snapshots = []

    def capture_scoped_at_point(self, x, y):
        self.points.append((x, y))
        return self.target

    def capture_event_source(self, source, *, settle_delay=0.08):
        self.sources.append((source, settle_delay))
        return self.target

    def capture_at_point_snapshot(self, x, y):
        self.point_snapshots.append((x, y))
        return self.target

    def capture_event_source_snapshot(self, source):
        self.source_snapshots.append(source)
        return self.source_target


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
    adapter._pointer_worker.start()
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=None,
    ))
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1r", detail1=125, detail2=240, source=None,
    ))
    adapter._pointer_worker.stop_and_drain()
    assert driver.point_snapshots == [(125, 240)]
    assert emitted[0].target.name == "Save"
    assert emitted[0].coordinates == (125, 240)


def test_pointer_release_uses_semantic_target_resolved_from_matching_press():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)
    emitted = []
    adapter._emit = emitted.append

    adapter._pointer_worker.start()
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=object(),
    ))
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1r", detail1=126, detail2=241, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert driver.sources == []
    assert len(driver.source_snapshots) == 1
    assert len(emitted) == 1
    assert emitted[0].target.name == "Save"
    assert emitted[0].phase == "released"
    assert emitted[0].coordinates == (126, 241)


def test_registry_pointer_callback_resolves_before_enqueuing_work():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)
    event = SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=object(),
    )

    assert adapter._pointer_worker.accept_pointer("mouse:button:1p", (1, 2), 1.0) is False
    adapter._pointer_worker.start()
    adapter._handle_pointer(event)

    assert driver.sources == []
    assert len(driver.source_snapshots) == 1
    adapter._pointer_worker.stop_and_drain()
    assert driver.sources == []
    assert adapter._pointer_worker.state == "stopped"
    assert adapter._pointer_worker.accept_pointer("mouse:button:1p", (1, 2), 2.0) is False


def test_pointer_press_coordinates_override_generic_shell_event_source():
    shell = CapturedComponent(
        name="GNOME Shell", role="application", description=None,
        accessible_id=None, application="gnome-shell", hierarchy=(), actions=(),
        bounds=(0, 0, 1920, 1080), state=ComponentState(True),
    )
    driver = _Driver(_target(), source_target=shell)
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)

    adapter._pointer_worker.start()
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=500, detail2=600, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert driver.point_snapshots == [(500, 600)]
    assert driver.sources == []


def test_desktop_frame_event_source_falls_through_to_clicked_component():
    desktop_frame = CapturedComponent(
        name="main", role="desktop frame", description=None,
        accessible_id="-1", application="Application Window", hierarchy=(),
        actions=(), bounds=(0, 0, 1024, 768), state=ComponentState(True),
    )
    driver = _Driver(_target(), source_target=desktop_frame)
    events = []
    adapter = AtspiRecordingAdapter(
        driver,
        on_resolved=lambda target, duration: events.append(target),
        acknowledgement_seconds=0,
    )
    adapter._pointer_worker.start()
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert len(driver.source_snapshots) == 1
    assert driver.point_snapshots == [(125, 240)]
    assert events == [_target()]
    assert not _is_recordable_target(desktop_frame)


def test_point_hit_testing_excludes_highlight_overlay_application_subtree():
    class Accessible:
        def __init__(self, name, role, bounds=None, children=()):
            self.name = name
            self.role = role
            self.bounds = bounds
            self.children = list(children)

        @property
        def childCount(self):
            return len(self.children)

        def getChildAtIndex(self, index):
            return self.children[index]

        def getRoleName(self):
            return self.role

        def queryComponent(self):
            return self

        def getExtents(self, coordinate_type):
            if self.bounds is None:
                raise RuntimeError("structural node has no component geometry")
            return SimpleNamespace(
                x=self.bounds[0], y=self.bounds[1],
                width=self.bounds[2], height=self.bounds[3],
            )

    button = Accessible("Open", "push button", (10, 20, 30, 40))
    target_app = Accessible("Target", "application", children=(button,))
    red_edge = Accessible("", "window", (9, 19, 32, 42))
    harness_app = Accessible("Automation Harness Author", "application", children=(red_edge,))
    desktop = Accessible("Desktop", "desktop", children=(target_app, harness_app))

    resolved = _deepest_at_point(
        desktop,
        x=12,
        y=22,
        pyatspi=SimpleNamespace(DESKTOP_COORDS=0),
        excluded_application_prefixes=("Automation Harness",),
    )

    assert resolved is button


def test_recording_and_next_click_use_the_same_canonical_click_snapshot():
    desktop_frame = replace(
        _target(), name="main", role="desktop frame", accessible_id="-1",
        application="Application Window", actions=(), bounds=(0, 0, 1024, 768),
    )

    class CanonicalDriver(AtspiDriver):
        available = True

        def capture_event_source_snapshot(self, source):
            return desktop_frame

        def capture_at_point_snapshot(self, x, y, *, excluded_application_prefixes=()):
            return _target()

    driver = CanonicalDriver()
    expected = driver.capture_click_snapshot(
        object(), (125, 240),
        excluded_application_prefixes=("Automation Harness",),
    )
    highlighted = []
    adapter = AtspiRecordingAdapter(
        driver,
        on_resolved=lambda target, duration: highlighted.append(target),
        acknowledgement_seconds=0,
    )
    adapter._pointer_worker.start()
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert highlighted == [expected]


def test_press_hold_retries_until_transient_target_becomes_accessible():
    class SettlingDriver(_Driver):
        def __init__(self):
            super().__init__(_target())
            self.attempts = 0

        def capture_click_snapshot(self, source, coordinates, *, excluded_application_prefixes=()):
            self.attempts += 1
            if self.attempts < 3:
                raise LookupError("transient menu item is not exposed yet")
            return self.target

    driver = SettlingDriver()
    highlighted = []
    adapter = AtspiRecordingAdapter(
        driver,
        on_resolved=lambda target, duration: highlighted.append(target),
        acknowledgement_seconds=0,
        hold_resolution_timeout=0.2,
        hold_retry_interval=0.001,
    )
    adapter._pointer_worker.start()
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert driver.attempts == 3
    assert highlighted == [_target()]


def test_press_hold_does_not_retry_authoring_controls():
    class ExcludedDriver(_Driver):
        def __init__(self):
            super().__init__(_target())
            self.attempts = 0

        def capture_click_snapshot(self, source, coordinates, *, excluded_application_prefixes=()):
            self.attempts += 1
            raise AtspiExcludedClickSource("authoring control")

    driver = ExcludedDriver()
    adapter = AtspiRecordingAdapter(
        driver,
        hold_resolution_timeout=1.0,
        hold_retry_interval=0.1,
    )
    adapter._pointer_worker.start()
    started = time.monotonic()
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=125, detail2=240, source=object(),
    ))
    elapsed = time.monotonic() - started
    adapter._pointer_worker.stop_and_drain()

    assert driver.attempts == 1
    assert elapsed < 0.05


def test_stop_request_cancels_an_unresolved_hold_retry():
    entered = threading.Event()

    class UnresolvedDriver(_Driver):
        def capture_click_snapshot(self, source, coordinates, *, excluded_application_prefixes=()):
            entered.set()
            raise LookupError("not exposed")

    adapter = AtspiRecordingAdapter(
        UnresolvedDriver(_target()),
        hold_resolution_timeout=2.0,
        hold_retry_interval=0.01,
    )
    resolving = threading.Thread(
        target=adapter._resolve_pointer_event,
        args=(SimpleNamespace(source=object()), (125, 240)),
    )
    resolving.start()
    assert entered.wait(1)
    adapter._stop_requested.set()
    resolving.join(0.1)

    assert not resolving.is_alive()


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
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=10, detail2=20, source=object(),
    ))
    # Acknowledgement is produced while recording is active, without relying
    # on stop_and_drain() to unblock semantic resolution.
    for _unused in range(100):
        if events:
            break
        time.sleep(0.001)
    assert events == [("highlight", "Save")]
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1r", detail1=10, detail2=20, source=object(),
    ))
    adapter._pointer_worker.stop_and_drain()

    assert events == [("highlight", "Save"), ("emit", "Save")]


def test_highlight_duration_does_not_block_rapid_transient_menu_targets():
    highlighted = []
    adapter = AtspiRecordingAdapter(
        _Driver(_target()),
        on_resolved=lambda target, duration: highlighted.append(target.name),
        acknowledgement_seconds=0.3,
    )
    adapter._pointer_worker.start()
    adapter._pointer_worker.accept_pointer(
        "mouse:button:1p", (10, 20), 1.0, _target(),
    )
    adapter._pointer_worker.accept_pointer(
        "mouse:button:1r", (10, 20), 1.01,
    )
    adapter._pointer_worker.accept_pointer(
        "mouse:button:1p", (12, 22), 1.02, replace(_target(), name="Open"),
    )

    deadline = time.monotonic() + 0.1
    while len(highlighted) < 2 and time.monotonic() < deadline:
        time.sleep(0.001)
    adapter._pointer_worker.stop_and_drain()

    assert highlighted == ["Save", "Open"]


def test_stop_returns_while_deferring_lease_release_until_active_resolution_finishes():
    entered = threading.Event()
    release = threading.Event()

    class BlockingDriver(_Driver):
        def capture_event_source_snapshot(self, source):
            entered.set()
            release.wait(1)
            return self.target

    class Lease:
        closed = False

        def close(self):
            self.closed = True

    registry = SimpleNamespace(deregisterEventListener=lambda callback, event_type: None)
    adapter = AtspiRecordingAdapter(BlockingDriver(_target()), acknowledgement_seconds=0)
    adapter._pyatspi = SimpleNamespace(Registry=registry)
    lease = Lease()
    adapter._lease = lease
    adapter._listeners = [(adapter._pointer, "mouse:button:1p")]
    adapter._pointer_worker.start()
    with adapter._callback_condition:
        adapter._callbacks_accepting = True

    callback = threading.Thread(target=adapter._pointer, args=(SimpleNamespace(
        type="mouse:button:1p", detail1=10, detail2=20, source=object(),
    ),))
    callback.start()
    assert entered.wait(1)

    stopping = threading.Thread(target=adapter.stop)
    stopping.start()
    stopping.join(1)
    assert not stopping.is_alive()
    assert not lease.closed

    release.set()
    callback.join(1)
    assert not callback.is_alive()
    for _unused in range(100):
        if lease.closed:
            break
        time.sleep(0.001)
    assert lease.closed


def test_action_and_text_events_drain_against_last_resolved_target():
    driver = _Driver(_target())
    adapter = AtspiRecordingAdapter(driver, acknowledgement_seconds=0)
    emitted = []
    adapter._emit = emitted.append
    adapter._pointer_worker.start()
    adapter._handle_pointer(SimpleNamespace(
        type="mouse:button:1p", detail1=10, detail2=20, source=object(),
    ))
    adapter._handle_pointer(SimpleNamespace(
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
