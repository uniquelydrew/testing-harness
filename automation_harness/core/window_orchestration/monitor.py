"""On-demand X11 window observation.

No background worker or focus-stealing loop: observation occurs at execution
boundaries and is injectable for deterministic tests. Snapshots are bounded.
"""
from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

from automation_harness.drivers import x11_foreground


@dataclass(frozen=True)
class WindowSnapshot:
    observed_at: float
    active_native_id: int | None
    available: bool
    diagnostic: str | None = None


@dataclass(frozen=True)
class WindowDescriptor:
    native_window_id: int
    process_id: int | None
    title: str


@dataclass(frozen=True)
class WindowLifecycleEvent:
    kind: str
    window: WindowDescriptor
    observed_at: float


class WindowMonitor:
    def __init__(self, *, foreground_reader: Callable[[], int | None] | None = None,
                 native_activator=None, window_lister=None, history_size: int = 64,
                 desktop_available: Callable[[], bool] | None = None):
        self._read = foreground_reader or x11_foreground.active_window
        self._activate = native_activator or x11_foreground.activate_window
        self._list = window_lister or x11_foreground.list_windows
        self._available = desktop_available or (lambda: bool(os.environ.get("DISPLAY")))
        self._history = deque(maxlen=history_size)
        self._lifecycle = deque(maxlen=history_size)
        self._inventory = None

    def snapshot(self) -> WindowSnapshot:
        if not self._available():
            result = WindowSnapshot(time.monotonic(), None, False, "X11 DISPLAY unavailable")
        else:
            try:
                active = self._read()
                result = WindowSnapshot(
                    time.monotonic(), active, active is not None,
                    None if active is not None else "window manager did not expose _NET_ACTIVE_WINDOW",
                )
            except Exception as exc:
                result = WindowSnapshot(
                    time.monotonic(), None, False, "%s: %s" % (type(exc).__name__, exc),
                )
        self._history.append(result)
        return result

    @property
    def desktop_available(self) -> bool:
        return bool(self._available())

    @property
    def history(self) -> tuple[WindowSnapshot, ...]:
        return tuple(self._history)

    @property
    def lifecycle(self) -> tuple[WindowLifecycleEvent, ...]:
        return tuple(self._lifecycle)

    def inventory(self) -> tuple[WindowDescriptor, ...]:
        """Observe managed windows and retain bounded open/close/title changes.

        This is an explicit poll, not a background process; calling code owns
        the cadence. A failed poll never implies all applications closed.
        """
        if not self._available():
            raise RuntimeError("window inventory requires X11 DISPLAY")
        found = {}
        for entry in self._list():
            xid = entry.get("native_window_id")
            if isinstance(xid, str):
                xid = int(xid, 16)
            if not isinstance(xid, int) or xid <= 0 or xid in found:
                raise RuntimeError("malformed or duplicate native window identity")
            found[xid] = WindowDescriptor(
                xid, entry.get("process_id"), str(entry.get("title") or ""),
            )
        previous = self._inventory
        observed_at = time.monotonic()
        if previous is not None:
            for xid in sorted(previous.keys() - found.keys()):
                self._lifecycle.append(WindowLifecycleEvent("closed", previous[xid], observed_at))
            for xid in sorted(found.keys() - previous.keys()):
                self._lifecycle.append(WindowLifecycleEvent("opened", found[xid], observed_at))
            for xid in sorted(found.keys() & previous.keys()):
                if found[xid] != previous[xid]:
                    self._lifecycle.append(WindowLifecycleEvent("changed", found[xid], observed_at))
        self._inventory = found
        return tuple(found[xid] for xid in sorted(found))

    def activate(self, *, pid: int, title: str, timeout: float = 2.0) -> dict:
        """Request and verify an exact PID/title pair using EWMH."""
        if not self._available():
            raise RuntimeError("native X11 activation requires DISPLAY")
        details = self._activate(pid=int(pid), title=title, timeout=timeout)
        if details.get("native_foreground_verified") is not True:
            raise RuntimeError("X11 activation returned no foreground verification")
        self.snapshot()
        return dict(details)

    def await_foreground_change(self, previous_native_id: int | None, *,
                                timeout: float = 2.0, poll_interval: float = 0.05) -> WindowSnapshot:
        """Observe a window transition; never manufacture or request one."""
        deadline = time.monotonic() + timeout
        while True:
            observed = self.snapshot()
            if not observed.available:
                raise RuntimeError(observed.diagnostic or "desktop foreground unavailable")
            if observed.active_native_id != previous_native_id:
                return observed
            if time.monotonic() >= deadline:
                raise TimeoutError("foreground window did not change within %.2fs" % timeout)
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
