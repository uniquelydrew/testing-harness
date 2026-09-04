import threading
import time

from automation_harness.recording.x11_pointer import X11PointerMonitor


class _Backend:
    def __init__(self, samples):
        self.samples = iter(samples)
        self.closed = False

    def open(self):
        pass

    def sample(self):
        try:
            return next(self.samples)
        except StopIteration:
            return (30, 40, 0)

    def close(self):
        self.closed = True


def test_x11_monitor_emits_press_while_button_is_still_held():
    # Initial up, then press, then two held samples, then release.
    backend = _Backend([
        (10, 20, 0),
        (11, 21, 1 << 8),
        (12, 22, 1 << 8),
        (13, 23, 1 << 8),
        (14, 24, 0),
    ])
    events = []
    press_seen = threading.Event()
    release_seen = threading.Event()

    def observed(event_type, coordinates, timestamp):
        events.append((event_type, coordinates, timestamp))
        if event_type.endswith("1p"):
            press_seen.set()
        if event_type.endswith("1r"):
            release_seen.set()

    monitor = X11PointerMonitor(lambda: backend, poll_interval=0.002)
    monitor.start(observed)
    assert press_seen.wait(0.1)
    assert not release_seen.is_set()
    assert release_seen.wait(0.1)
    monitor.stop()

    assert [event[0] for event in events] == [
        "mouse:button:1p", "mouse:button:1r",
    ]
    assert events[0][1] == (11, 21)
    assert events[1][1] == (14, 24)
    assert backend.closed


def test_x11_monitor_stop_is_bounded():
    backend = _Backend([(10, 20, 0)])
    monitor = X11PointerMonitor(lambda: backend, poll_interval=0.01)
    monitor.start(lambda *_args: None)
    started = time.monotonic()
    monitor.stop()
    assert time.monotonic() - started < 0.2
    assert backend.closed
