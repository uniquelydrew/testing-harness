"""Foreground preconditions for live-desktop semantic actions.

Native window identity is preferred. Bridge-reported focus can prove activation
only when X11 verification is unavailable or no PID/window pair is exposed.
Unknown or explicitly negative activation outcomes are never accepted.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from automation_harness.core.window_orchestration.monitor import WindowMonitor


class WindowOrchestrationError(RuntimeError):
    pass


def _positive_focus(details: Mapping[str, Any]) -> bool:
    if details.get("native_foreground_verified") is True or details.get("focused") is True:
        return True
    component = details.get("component")
    if isinstance(component, Mapping):
        state = component.get("state")
        if isinstance(state, Mapping) and state.get("focused") is True:
            return True
    return False


def _expected_window(arguments: Mapping[str, Any]) -> str | None:
    direct = arguments.get("window")
    if isinstance(direct, str) and direct:
        return direct
    identity = arguments.get("identification")
    if isinstance(identity, Mapping):
        for source in (identity.get("mandatory"), identity.get("assistive"), identity):
            if isinstance(source, Mapping):
                title = source.get("window")
                if isinstance(title, str) and title:
                    return title
    return None


class WindowOrchestrator:
    """One instance per execution context; no dependency on GTK or the runner."""

    def __init__(self, monitor: WindowMonitor | None = None):
        self.monitor = monitor or WindowMonitor()
        self.last_activation: dict[str, Any] | None = None

    def ensure_foreground(self, *, component_id: str, object_id: str,
                          driver: Any, strategy: str,
                          arguments: Mapping[str, Any],
                          timeout: float = 2.0) -> dict[str, Any]:
        activate = getattr(driver, "activate_window", None)
        if not callable(activate):
            raise WindowOrchestrationError(
                "%s does not support owning-window activation for %r" % (strategy, component_id)
            )
        started = time.monotonic()
        expected_window = _expected_window(arguments)
        try:
            # Reuse a previously verified foreground only if the native active
            # window still matches. This avoids raising the same Stage twice
            # when the runner prepares screenshot evidence before execution.
            last = self.last_activation
            if (last is not None and last.get("object_id") == object_id
                    and (expected_window is None or last.get("window") == expected_window)
                    and self.monitor.desktop_available
                    and last.get("native_window_id")):
                observed = self.monitor.snapshot()
                if observed.available and observed.active_native_id == int(
                    last["native_window_id"], 16,
                ):
                    return dict(last, reused_foreground=True)
            reported = activate(**dict(arguments))
            if not isinstance(reported, Mapping):
                raise WindowOrchestrationError("driver returned no window activation evidence")
            details = dict(reported)
            reported_window = details.get("window")
            if (expected_window and reported_window
                    and expected_window != reported_window):
                raise WindowOrchestrationError(
                    "driver activated %r instead of owning window %r" %
                    (reported_window, expected_window)
                )
            identity = expected_window or reported_window
            pid = details.get("bridge_pid") or details.get("process_id") or details.get("pid")
            # Native verification is mandatory when a concrete desktop
            # identity is available. The bridge's claim alone is not enough.
            if (isinstance(pid, int) and pid > 0 and isinstance(identity, str)
                    and identity and self.monitor.desktop_available):
                native = self.monitor.activate(pid=pid, title=identity, timeout=timeout)
                details.update(native)
                details["verification"] = "x11-ewmh"
            elif not _positive_focus(details):
                raise WindowOrchestrationError(
                    "activation of %r has no verifiable foreground evidence "
                    "(driver=%s, window=%r, pid=%r)" %
                    (component_id, strategy, identity, pid)
                )
            else:
                details["verification"] = "backend-confirmed"
            if identity:
                details["window"] = identity
            details.update({
                "component_id": component_id,
                "object_id": object_id,
                "driver": strategy,
                "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
            })
            self.last_activation = details
            return details
        except Exception as exc:
            raise WindowOrchestrationError(
                "cannot establish foreground for %r (driver=%s): %s: %s" %
                (component_id, strategy, type(exc).__name__, exc)
            ) from exc

    def await_window(self, *, title: str, process_id: int | None = None,
                     timeout: float = 2.0, poll_interval: float = 0.05):
        """Wait for one showing native owner; ambiguous matches fail closed."""
        if not title:
            raise ValueError("await_window requires a nonempty window title")
        deadline = time.monotonic() + timeout
        while True:
            matches = tuple(
                item for item in self.monitor.inventory()
                if item.title == title and
                (process_id is None or item.process_id == process_id)
            )
            if len(matches) > 1:
                raise WindowOrchestrationError(
                    "ambiguous native windows for title=%r pid=%r: %s" %
                    (title, process_id, [hex(item.native_window_id) for item in matches])
                )
            if matches:
                return matches[0]
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    "expected window %r pid=%r did not appear within %.2fs" %
                    (title, process_id, timeout)
                )
            time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

    def snapshot(self):
        return self.monitor.snapshot()

    def await_transition(self, previous_native_id, *, timeout: float = 2.0):
        return self.monitor.await_foreground_change(previous_native_id, timeout=timeout)
