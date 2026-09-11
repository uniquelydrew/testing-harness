from __future__ import annotations

import os
import queue
import threading
from typing import Any

from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService
from automation_harness.drivers.atspi_driver import (
    AtspiDriver,
    _accessible_signature,
    _application_name,
    _device_event_coordinates,
    _focused_accessible,
    _pyatspi,
)
from automation_harness.drivers.atspi_registry import acquire_atspi_registry
from automation_harness.recording.x11_pointer import X11PointerMonitor, _XlibPointerBackend


_INSTALLED = False
_ORIGINAL_CAPTURE_NEXT_CLICK = AtspiDriver.capture_next_click
_ORIGINAL_HYBRID_CAPTURE_NEXT_CLICK = HybridObjectCaptureService.capture_next_click
_ORIGINAL_HYBRID_CAPTURE_AT_POINT = HybridObjectCaptureService.capture_at_point
_ORIGINAL_HYBRID_CAPTURE_SCOPED_AT_POINT = HybridObjectCaptureService.capture_scoped_at_point


def install() -> None:
    """Make X11 capture observe physical input without relying on target events.

    AT-SPI device/focus events are not a reliable press clock on the target RHEL
    desktop. Recording already uses XQueryPointer to observe the physical press,
    uses X11's owning PID for z-order arbitration, asks the native JavaFX bridge
    for that process first, and only then falls back to AT-SPI. Capture Next
    Click uses that same mechanism.

    Pointer/hotkey capture follows the same ownership rule. This matters for
    instrumented JavaFX applications: ObjectCaptureService's inherited point
    capture is AT-SPI-only and cannot see the JavaFX scene graph on Linux.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    def capture_next_click(self, *, timeout: float = 30.0):
        session = str(os.environ.get("XDG_SESSION_TYPE") or "").strip().casefold()
        if session not in {"x11", "xorg"}:
            return _ORIGINAL_CAPTURE_NEXT_CLICK(self, timeout=timeout)
        return _capture_x11_mouse_press(self, timeout=timeout)

    def hybrid_capture_next_click(self, *, timeout: float = 30.0):
        session = str(os.environ.get("XDG_SESSION_TYPE") or "").strip().casefold()
        if session not in {"x11", "xorg"}:
            return _ORIGINAL_HYBRID_CAPTURE_NEXT_CLICK(self, timeout=timeout)
        return _capture_hybrid_x11_mouse_press(self, timeout=timeout)

    def hybrid_capture_at_point(self, x, y):
        session = str(os.environ.get("XDG_SESSION_TYPE") or "").strip().casefold()
        if session not in {"x11", "xorg"}:
            return _ORIGINAL_HYBRID_CAPTURE_AT_POINT(self, x, y)
        return _resolve_hybrid_point(self, (int(x), int(y)), scoped=False)

    def hybrid_capture_scoped_at_point(self, x, y):
        session = str(os.environ.get("XDG_SESSION_TYPE") or "").strip().casefold()
        if session not in {"x11", "xorg"}:
            return _ORIGINAL_HYBRID_CAPTURE_SCOPED_AT_POINT(self, x, y)
        return _resolve_hybrid_point(self, (int(x), int(y)), scoped=True)

    AtspiDriver.capture_next_click = capture_next_click
    HybridObjectCaptureService.capture_next_click = hybrid_capture_next_click
    HybridObjectCaptureService.capture_at_point = hybrid_capture_at_point
    HybridObjectCaptureService.capture_scoped_at_point = hybrid_capture_scoped_at_point
    _INSTALLED = True


def _capture_hybrid_x11_mouse_press(service: HybridObjectCaptureService, *, timeout: float):
    """Capture one physical X11 press and resolve it exactly as recording does."""
    if timeout <= 0:
        raise ValueError("click capture timeout must be positive")

    outcome = queue.Queue(maxsize=1)
    completed = threading.Event()
    finish_lock = threading.Lock()
    # Capture has a short bounded lifetime, so poll aggressively. XQueryPointer
    # observes server state and cannot be consumed by the application under test.
    monitor = X11PointerMonitor(poll_interval=0.002)

    def finish(captured, error):
        with finish_lock:
            if completed.is_set():
                return
            outcome.put((captured, error))
            completed.set()

    def on_pointer(event_type, coordinates, _timestamp, owner_pid=None):
        if not event_type.endswith("1p"):
            return
        try:
            captured = _resolve_hybrid_press(service, coordinates, owner_pid)
            if captured is None:
                return
        except BaseException as exc:
            finish(None, exc)
            return
        finish(captured, None)

    monitor.start(on_pointer)
    try:
        if not completed.wait(timeout):
            finish(None, TimeoutError("no resolvable object was clicked before capture timed out"))
    finally:
        monitor.stop()

    try:
        captured, error = outcome.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError("X11 click capture stopped without a result") from exc
    if error is not None:
        raise error
    if captured is None:
        raise RuntimeError("X11 click capture returned no object")
    return captured


def _resolve_hybrid_press(service, coordinates, owner_pid):
    """Resolve the topmost X11 client using native semantics before AT-SPI."""
    return _resolve_hybrid_point(
        service,
        coordinates,
        scoped=True,
        owner_pid=owner_pid,
    )


def _resolve_hybrid_point(service, coordinates, scoped=True, owner_pid=None):
    """Resolve a point using X11 ownership, JavaFX semantics, then AT-SPI.

    The owning X11 PID prevents an obscured JavaFX window from winning a hit
    test merely because its scene bounds contain the same screen coordinate.
    When point capture was initiated by a hotkey, ownership is sampled directly
    from the X server; no key or mouse event from the target process is needed.
    """
    x, y = (int(coordinates[0]), int(coordinates[1]))
    errors = []
    if owner_pid is None:
        owner_pid = _owner_pid_at_current_pointer(x, y)

    javafx_available = False
    try:
        javafx_available = bool(service.javafx_driver.available)
    except Exception as exc:
        errors.append("javafx availability: %s: %s" % (type(exc).__name__, exc))

    if owner_pid is not None and javafx_available:
        try:
            captured = service.javafx_driver.capture_at_point(x, y, process_id=owner_pid)
            if _captured_process_id(captured) == owner_pid and not _authoring_chrome(captured):
                return captured
        except Exception as exc:
            errors.append("javafx pid %s: %s: %s" % (owner_pid, type(exc).__name__, exc))

    atspi_candidate = None
    if bool(getattr(service.driver, "available", False)):
        try:
            if scoped:
                snapshot = getattr(service.driver, "capture_at_point_snapshot", None)
                if snapshot is not None:
                    atspi_candidate = snapshot(
                        x, y,
                        excluded_application_prefixes=("Automation Harness",),
                    )
                else:
                    atspi_candidate = service.driver.capture_scoped_at_point(x, y)
            else:
                atspi_candidate = service.driver.capture_at_point(x, y)
            if _authoring_chrome(atspi_candidate):
                atspi_candidate = None
            elif owner_pid is not None:
                atspi_pid = _captured_process_id(atspi_candidate)
                if atspi_pid is not None and atspi_pid != owner_pid:
                    atspi_candidate = None
        except Exception as exc:
            errors.append("atspi: %s: %s" % (type(exc).__name__, exc))
            atspi_candidate = None

    if atspi_candidate is not None and javafx_available:
        atspi_pid = _captured_process_id(atspi_candidate)
        if atspi_pid is not None:
            try:
                captured = service.javafx_driver.capture_at_point(x, y, process_id=atspi_pid)
                if not _authoring_chrome(captured):
                    return captured
            except Exception as exc:
                errors.append("javafx atspi pid %s: %s: %s" % (atspi_pid, type(exc).__name__, exc))

    if atspi_candidate is not None:
        return atspi_candidate

    if owner_pid is None and javafx_available:
        try:
            captured = service.javafx_driver.capture_at_point(x, y)
            if not _authoring_chrome(captured):
                return captured
        except Exception as exc:
            errors.append("javafx unscoped: %s: %s" % (type(exc).__name__, exc))

    raise LookupError(
        "no live object resolved at (%s, %s)%s" %
        (x, y, ("; " + "; ".join(errors)) if errors else "")
    )


def _owner_pid_at_current_pointer(expected_x, expected_y):
    backend = _XlibPointerBackend()
    try:
        backend.open()
        sample = backend.sample()
    except Exception:
        return None
    finally:
        try:
            backend.close()
        except Exception:
            pass
    x, y = int(sample[0]), int(sample[1])
    if abs(x - int(expected_x)) > 3 or abs(y - int(expected_y)) > 3:
        return None
    try:
        value = int(sample[3]) if len(sample) > 3 and sample[3] is not None else None
    except (TypeError, ValueError):
        return None
    return value if value and value > 0 else None


def _captured_process_id(captured):
    properties = dict(getattr(captured, "backend_properties", {}) or {})
    for key in ("bridge_pid", "process_id", "process-id", "pid"):
        try:
            value = int(properties.get(key))
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def _authoring_chrome(captured):
    application = str(getattr(captured, "application", None) or "")
    name = str(getattr(captured, "name", None) or "")
    return application.startswith("Automation Harness") or name == "Stop Recording"


def _capture_x11_mouse_press(driver: AtspiDriver, *, timeout: float):
    """AT-SPI-only compatibility path for legacy callers."""
    if timeout <= 0:
        raise ValueError("click capture timeout must be positive")

    pyatspi = _pyatspi()
    baseline_focus = _focused_accessible(pyatspi.Registry.getDesktop(0), pyatspi)
    baseline_signature = (
        _accessible_signature(baseline_focus, pyatspi)
        if baseline_focus is not None else None
    )
    outcome = queue.Queue(maxsize=1)
    completed = threading.Event()
    finish_lock = threading.Lock()

    def finish(captured, error):
        with finish_lock:
            if completed.is_set():
                return
            outcome.put((captured, error))
            completed.set()

    def on_mouse_press(event: Any) -> None:
        source = getattr(event, "source", None)
        if source is None:
            return
        application = str(_application_name(source) or "")
        if application.startswith("Automation Harness"):
            return
        if (
            baseline_signature is not None
            and _accessible_signature(source, pyatspi) == baseline_signature
        ):
            pass
        try:
            captured = driver.capture_click_snapshot(
                source,
                _device_event_coordinates(event),
                excluded_application_prefixes=("Automation Harness", "gnome-shell"),
            )
        except BaseException as exc:
            if application == "gnome-shell":
                return
            finish(None, exc)
            return
        finish(captured, None)

    event_type = "mouse:button:1p"
    pyatspi.Registry.registerEventListener(on_mouse_press, event_type)
    try:
        lease = acquire_atspi_registry(pyatspi)
        try:
            if not completed.wait(timeout):
                finish(None, TimeoutError("no object was clicked before capture timed out"))
        finally:
            lease.close()
    finally:
        try:
            pyatspi.Registry.deregisterEventListener(on_mouse_press, event_type)
        except Exception:
            pass

    try:
        captured, error = outcome.get_nowait()
    except queue.Empty as exc:
        raise RuntimeError("AT-SPI click capture stopped without a result") from exc
    if error is not None:
        raise error
    if captured is None:
        raise RuntimeError("AT-SPI click capture returned no object")
    return captured
