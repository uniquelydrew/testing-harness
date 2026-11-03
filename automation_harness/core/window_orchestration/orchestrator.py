"""Foreground preconditions for live-desktop semantic actions.

Native window identity is preferred. Bridge-reported focus can prove activation
only when X11 verification is unavailable or no PID/window pair is exposed.
Unknown or explicitly negative activation outcomes are never accepted.
"""
from __future__ import annotations

import time
from typing import Any, Mapping

from automation_harness.core.window_orchestration.monitor import WindowMonitor
from automation_harness.drivers.x11_foreground import X11WindowNotFound


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
            # JavaFX focus acquisition is a two-phase handshake. A Stage can
            # acknowledge toFront()/requestFocus() while an unrelated terminal
            # still owns native foreground. Never block inside the JavaFX
            # driver's focused-state retry before attempting the X11 handoff.
            request_activation = (
                getattr(driver, "request_window_activation", None)
                if strategy == "javafx" else None
            )
            phased = callable(request_activation)
            reported = (
                request_activation(**dict(arguments))
                if phased else activate(**dict(arguments))
            )
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
            # The driver has located a single owning JavaFX Stage, but its
            # initial focus request can be denied while a terminal is in
            # front. Raise the native window BEFORE waiting for Stage focus.
            # Stage identity must be verified again after native activation.
            native_not_found = None
            if (isinstance(pid, int) and pid > 0 and isinstance(identity, str)
                    and identity and self.monitor.desktop_available):
                try:
                    native = self.monitor.activate(pid=pid, title=identity, timeout=timeout)
                except X11WindowNotFound as exc:
                    native_not_found = exc
                    # Only a uniquely resolved JavaFX Stage can independently
                    # establish focus when the WM exposes no matching XID.
                    if not (strategy == "javafx"
                            and details.get("selection") == "owning_window"
                            and reported_window == identity):
                        raise
                else:
                    details.update(native)
                    details["verification"] = "x11-ewmh"

            if phased:
                # This strict second request confirms that the exact Stage
                # has actually become focused following the native handoff.
                # Never conflate a successful X11 request with JavaFX focus.
                try:
                    verified = activate(**dict(arguments))
                except Exception as exc:
                    if native_not_found is not None:
                        raise WindowOrchestrationError(
                            "native X11 owner is unavailable (%s); "
                            "JavaFX Stage did not confirm focus (%s: %s)" %
                            (native_not_found, type(exc).__name__, exc)
                        ) from exc
                    raise
                if not isinstance(verified, Mapping):
                    raise WindowOrchestrationError(
                        "JavaFX Stage focus verification returned no evidence"
                    )
                if verified.get("window") != identity or verified.get("bridge_pid") != pid:
                    raise WindowOrchestrationError(
                        "JavaFX Stage identity changed during foreground handoff "
                        "(expected pid=%r window=%r; actual pid=%r window=%r)" %
                        (pid, identity, verified.get("bridge_pid"), verified.get("window"))
                    )
                if verified.get("focused") is not True:
                    raise WindowOrchestrationError(
                        "JavaFX Stage did not confirm focus after native handoff"
                    )
                details["focused"] = True
                details["activation_attempts"] = (
                    details.get("activation_attempts", 0) +
                    verified.get("activation_attempts", 0)
                )

            if native_not_found is not None:
                if not (strategy == "javafx"
                        and details.get("selection") == "owning_window"
                        and details.get("focused") is True
                        and reported_window == identity):
                    raise native_not_found
                details["verification"] = "javafx-stage-focused"
                details["native_foreground_verified"] = False
                details["native_lookup_diagnostic"] = (
                    "%s: %s" % (type(native_not_found).__name__, native_not_found)
                )
            elif "verification" not in details:
                if not _positive_focus(details):
                    raise WindowOrchestrationError(
                        "activation of %r has no verifiable foreground evidence "
                        "(driver=%s, window=%r, pid=%r)" %
                        (component_id, strategy, identity, pid)
                    )
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
