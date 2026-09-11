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
from automation_harness.recording.x11_pointer import X11PointerMonitor


_INSTALLED = False
_ORIGINAL_CAPTURE_NEXT_CLICK = AtspiDriver.capture_next_click
_ORIGINAL_HYBRID_CAPTURE_NEXT_CLICK = HybridObjectCaptureService.capture_next_click


def install() -> None:
    """Make X11 click capture use the same physical press clock as recording.

    AT-SPI device/focus events are not a reliable press clock on the target RHEL
    desktop. Recording already uses XQueryPointer to observe the physical press,
    uses X11's owning PID for z-order arbitration, asks the native JavaFX bridge
    for that process first, and only then falls back to AT-SPI. Capture Next
    Click must use that same mechanism rather than racing unrelated event paths.
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

    AtspiDriver.capture_next_click = capture_next_click
    HybridObjectCaptureService.capture_next_click = hybrid_capture_next_click
    _INSTALLED = True


def _capture_hybrid_x11_mouse_press(service: HybridObjectCaptureService, *, timeout: float):
    """Capture one physical X11 press and resolve it exactly as recording does."""
    if timeout <= 0:
        raise ValueError("click capture timeout must be positive")

    outcome = queue.Queue(maxsize=1)
    completed = threading.Event()
    finish_lock = threading.Lock()
    monitor = X11PointerMonitor()

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
    """Resolve the topmost X11 client using native semantics before AT-SPI.

    This mirrors AtspiRecordingAdapter._resolve_physical_pointer_target. A
    covered JavaFX endpoint cannot win merely because its bounds contain the
    pointer: when X11 supplies an owning PID, only that process is eligible.
    """
    if owner_pid is not None:
        try:
            captured = service.javafx_driver.capture_at_point(
                *coordinates, process_id=owner_pid,
            )
            if _captured_process_id(captured) == owner_pid and not _authoring_chrome(captured):
                return captured
        except Exception:
            pass

    atspi_candidate = None
    if bool(getattr(service.driver, "available", False)):
        try:
            snapshot = getattr(service.driver, "capture_at_point_snapshot", None)
            if snapshot is not None:
                atspi_candidate = snapshot(
                    *coordinates,
                    excluded_application_prefixes=("Automation Harness",),
                )
            else:
                atspi_candidate = service.driver.capture_scoped_at_point(*coordinates)
            if _authoring_chrome(atspi_candidate):
                atspi_candidate = None
            elif owner_pid is not None:
                atspi_pid = _captured_process_id(atspi_candidate)
                if atspi_pid is not None and atspi_pid != owner_pid:
                    atspi_candidate = None
        except Exception:
            atspi_candidate = None

    if owner_pid is None and atspi_candidate is not None:
        atspi_pid = _captured_process_id(atspi_candidate)
        if atspi_pid is not None:
            try:
                captured = service.javafx_driver.capture_at_point(
                    *coordinates, process_id=atspi_pid,
                )
                if not _authoring_chrome(captured):
                    return captured
            except Exception:
                pass

    return atspi_candidate


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
    """AT-SPI-only compatibility path for legacy callers.

    The routed authoring application uses HybridObjectCaptureService and thus
    the physical X11 path above. This remains for direct AtspiDriver consumers.
    """
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
            # A real pointer press on the previously focused object is valid.
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
