import threading

from automation_harness.drivers.atspi_registry import _AtspiRegistryLoop


class _Registry:
    def __init__(self):
        self.starts = 0
        self.stops = 0
        self.started = threading.Event()
        self.stopped = threading.Event()

    def start(self):
        self.starts += 1
        self.started.set()
        self.stopped.wait()

    def stop(self):
        self.stops += 1
        self.stopped.set()


def test_registry_loop_is_reference_counted_across_clients():
    registry = _Registry()
    owner = _AtspiRegistryLoop(registry)
    first = owner.acquire()
    second = owner.acquire()

    first.close()
    assert registry.starts == 1
    assert registry.stops == 0

    second.close()
    assert registry.stops == 1


def test_registry_lease_close_is_idempotent():
    registry = _Registry()
    lease = _AtspiRegistryLoop(registry).acquire()
    lease.close()
    lease.close()
    assert registry.stops == 1
