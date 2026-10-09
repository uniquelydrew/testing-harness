"""Process-level ownership for the blocking AT-SPI registry event loop.

The pyatspi Registry is process-global native state. Repeatedly stopping and
restarting it between short-lived authoring operations is unsafe: a native
dispatch thread can still be unwinding when another client calls start().
Leases therefore control client ownership only. Once started, the registry
remains alive for the process lifetime and is stopped explicitly during
application shutdown.
"""
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
        if not self._started.wait(timeout=1.0):
            self.release()
            raise RuntimeError("AT-SPI registry event loop did not start")
        return AtspiRegistryLease(self)

    def release(self) -> None:
        with self._lock:
            if self._users:
                self._users -= 1
        # Deliberately do not stop the process-global native registry here.
        # Another capture/recording operation may start immediately after this
        # lease closes. Restarting Registry.start() while Registry.stop() is
        # still unwinding is a known native crash hazard.

    def shutdown(self, timeout: float = 2.0) -> bool:
        """Stop the native registry once, after every authoring client drained."""
        with self._lock:
            if self._users:
                return False
            thread = self._thread
            if thread is None:
                return True
            self.registry.stop()
        thread.join(timeout=max(0.0, float(timeout)))
        if thread.is_alive():
            return False
        with self._lock:
            if self._thread is thread:
                self._thread = None
        return True

    def _run(self) -> None:
        self._started.set()
        self.registry.start()


_loops_lock = threading.Lock()
_loops: dict[int, _AtspiRegistryLoop] = {}


def acquire_atspi_registry(pyatspi: Any) -> AtspiRegistryLease:
    """Keep one process-global registry loop alive across authoring operations."""
    registry = pyatspi.Registry
    key = id(registry)
    with _loops_lock:
        owner = _loops.get(key)
        if owner is None or owner.registry is not registry:
            owner = _AtspiRegistryLoop(registry)
            _loops[key] = owner
    return owner.acquire()


def shutdown_atspi_registries(timeout: float = 2.0) -> bool:
    """Stop idle registry loops during final application teardown.

    Returns False rather than forcing native teardown if any client still owns
    a lease or a registry dispatch thread does not terminate within the bound.
    """
    with _loops_lock:
        owners = tuple(_loops.values())
    success = True
    for owner in owners:
        if not owner.shutdown(timeout=timeout):
            success = False
    return success
