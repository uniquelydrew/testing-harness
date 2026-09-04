"""Process-level ownership for the blocking AT-SPI registry event loop."""
from __future__ import annotations

import threading
from typing import Any


class AtspiRegistryLease:
    def __init__(self, owner: "_AtspiRegistryLoop") -> None:
        self._owner = owner
        self._closed = False
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._owner.release()


class _AtspiRegistryLoop:
    def __init__(self, registry: Any) -> None:
        self.registry = registry
        self._lock = threading.Lock()
        self._users = 0
        self._thread: threading.Thread | None = None
        self._started = threading.Event()

    def acquire(self) -> AtspiRegistryLease:
        with self._lock:
            self._users += 1
            if self._thread is None:
                self._started.clear()
                self._thread = threading.Thread(
                    target=self._run,
                    name="automation-atspi-registry",
                    daemon=True,
                )
                self._thread.start()
            self._started.wait(timeout=1.0)
        return AtspiRegistryLease(self)

    def release(self) -> None:
        with self._lock:
            if self._users == 0:
                return
            self._users -= 1
            if self._users != 0 or self._thread is None:
                return
            thread = self._thread
            try:
                self.registry.stop()
            finally:
                thread.join(timeout=2.0)
                self._thread = None

    def _run(self) -> None:
        self._started.set()
        self.registry.start()


_loops_lock = threading.Lock()
_loops: dict[int, _AtspiRegistryLoop] = {}


def acquire_atspi_registry(pyatspi: Any) -> AtspiRegistryLease:
    """Keep the process-global registry alive until every client releases it."""
    registry = pyatspi.Registry
    key = id(registry)
    with _loops_lock:
        owner = _loops.get(key)
        if owner is None or owner.registry is not registry:
            owner = _AtspiRegistryLoop(registry)
            _loops[key] = owner
    return owner.acquire()
