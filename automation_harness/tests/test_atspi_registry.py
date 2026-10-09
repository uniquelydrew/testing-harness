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


def test_registry_loop_is_shared_across_sequential_clients():
    registry = _Registry()
    owner = _AtspiRegistryLoop(registry)
    first = owner.acquire()
    second = owner.acquire()

    first.close()
    second.close()
    assert registry.starts == 1
    assert registry.stops == 0

    third = owner.acquire()
    third.close()
    assert registry.starts == 1
    assert registry.stops == 0

    assert owner.shutdown()
    assert registry.stops == 1


def test_registry_lease_close_is_idempotent():
    registry = _Registry()
    owner = _AtspiRegistryLoop(registry)
    lease = owner.acquire()
    lease.close()
    lease.close()
    assert registry.stops == 0
    assert owner.shutdown()
    assert registry.stops == 1


def test_registry_refuses_shutdown_while_client_is_active():
    registry = _Registry()
    owner = _AtspiRegistryLoop(registry)
    lease = owner.acquire()

    assert owner.shutdown() is False
    assert registry.stops == 0

    lease.close()
    assert owner.shutdown()
    assert registry.stops == 1


def test_registry_never_restarts_between_short_lived_operations():
    registry = _Registry()
    owner = _AtspiRegistryLoop(registry)

    for _index in range(20):
        lease = owner.acquire()
        lease.close()

    assert registry.starts == 1
    assert registry.stops == 0
    assert owner.shutdown()
    assert registry.stops == 1
