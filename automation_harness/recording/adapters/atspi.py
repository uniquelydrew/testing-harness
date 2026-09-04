"""AT-SPI event adapter for recording native GTK and Swing interactions."""
from __future__ import annotations

import queue
import threading
import time
from typing import Any, Callable

from automation_harness.drivers.atspi_driver import AtspiDriver, _capture_semantic_accessible, _pyatspi
from automation_harness.drivers.atspi_registry import AtspiRegistryLease, acquire_atspi_registry
from automation_harness.recording.observations import ActionFired, Observation, PointerInteraction, StateChanged, TextChanged


_STRUCTURAL_ROLES = frozenset({
    "application", "application window", "desktop", "desktop frame", "frame",
    "window", "root pane", "dialog", "alert", "file chooser",
})
_PRESENTATION_CONTAINER_ROLES = frozenset({"panel", "filler", "section", "unknown"})
_PASSIVE_ROLES = frozenset({"label", "static", "paragraph", "icon", "image"})


def _normalize_role(value) -> str:
    return " ".join(str(value or "").replace("_", " ").replace("-", " ").casefold().split())


class _PointerRecordingWorker:
    """Single-owner pointer resolver with explicit drain-safe lifecycle."""

    READY = "ready"
    RESOLVING = "resolving"
    ACKNOWLEDGING = "acknowledging"
    DRAINING = "draining"
    STOPPED = "stopped"
    _STOP = object()

    def __init__(self, publish, acknowledge=None, acknowledgement_seconds=0.3):
        self._publish = publish
        self._acknowledge = acknowledge
        self._acknowledgement_seconds = max(0.0, float(acknowledgement_seconds))
        self._queue = queue.Queue()
        self._lock = threading.Lock()
        self._state = self.STOPPED
        self._accepting = False
        self._thread = None
        self._pressed = {}
        self._last_target = None
        self._errors = []

    @property
    def state(self):
        with self._lock:
            return self._state

    def start(self):
        with self._lock:
            if self._thread is not None:
                raise RuntimeError("pointer recording worker is already active")
            self._queue = queue.Queue()
            self._pressed = {}
            self._last_target = None
            self._errors = []
            self._accepting = True
            self._state = self.READY
            self._thread = threading.Thread(
                target=self._run, name="atspi-pointer-recording", daemon=True,
            )
            self._thread.start()

    @property
    def errors(self):
        with self._lock:
            return tuple(self._errors)

    def _accept(self, command):
        with self._lock:
            if not self._accepting:
                return False
            self._queue.put(command)
            return True

    def accept_pointer(self, event_type, coordinates, timestamp, target=None):
        return self._accept(("pointer", event_type, coordinates, timestamp, target))

    def accept_action(self, property_name, selected, event_type, timestamp):
        return self._accept(("action", property_name, selected, event_type, timestamp))

    def accept_text(self, after, event_type, timestamp):
        return self._accept(("text", after, event_type, timestamp))

    def stop_and_drain(self):
        with self._lock:
            worker = self._thread
            if worker is None:
                return
            self._accepting = False
            self._state = self.DRAINING
            self._queue.put(self._STOP)
        # Called by RecordingSession.stop() on its background shutdown thread;
        # waiting here cannot block GTK or the AT-SPI registry callback.
        worker.join()
        with self._lock:
            self._thread = None

    def _set_state(self, state):
        with self._lock:
            # DRAINING describes admission state and must remain visible while
            # already accepted commands finish.
            if self._state != self.DRAINING:
                self._state = state

    def _run(self):
        try:
            while True:
                item = self._queue.get()
                if item is self._STOP:
                    return
                try:
                    self._process(item)
                except Exception as exc:
                    with self._lock:
                        self._errors.append((type(exc).__name__, str(exc)))
        finally:
            self._pressed = {}
            with self._lock:
                self._state = self.STOPPED

    def _process(self, item):
        kind = item[0]
        if kind == "action":
            _kind, property_name, selected, event_type, timestamp = item
            target = self._last_target
            if target is not None:
                self._publish(StateChanged(
                    timestamp, "atspi", target, {"event_type": event_type},
                    property_name, not selected, selected,
                ))
                self._publish(ActionFired(
                    timestamp, "atspi", target, {"event_type": event_type},
                    "toggle" if property_name == "checked" else "select",
                ))
            return
        if kind == "text":
            _kind, after, event_type, timestamp = item
            if self._last_target is not None and after is not None:
                self._publish(TextChanged(
                    timestamp, "atspi", self._last_target,
                    {"event_type": event_type}, None, str(after),
                ))
            return
        _kind, event_type, coordinates, timestamp, resolved_target = item
        button = "secondary" if event_type.endswith(("3p", "3r")) else "primary"
        if event_type.endswith(("1p", "3p")):
            self._set_state(self.RESOLVING)
            target = resolved_target
            if target is not None:
                self._pressed[button] = target
                self._last_target = target
                self._set_state(self.ACKNOWLEDGING)
                if self._acknowledge is not None:
                    self._acknowledge(target, self._acknowledgement_seconds)
            self._set_state(self.READY)
            return
        target = self._pressed.pop(button, None)
        if target is None:
            target = resolved_target
        if target is not None:
            self._publish(PointerInteraction(
                timestamp, "atspi", target,
                {"event_type": event_type, "coordinates": coordinates},
                button, "released", coordinates,
            ))
        self._set_state(self.READY)


def _is_recordable_target(captured) -> bool:
    """Reject authoring chrome and non-interactive accessibility skin nodes."""
    application = str(getattr(captured, "application", None) or "")
    name = str(getattr(captured, "name", None) or "")
    role = _normalize_role(getattr(captured, "role", None))
    actions = tuple(getattr(captured, "actions", ()) or ())
    if application.startswith("Automation Harness") or name == "Stop Recording":
        return False
    if role in _STRUCTURAL_ROLES:
        return False
    if role in (_PRESENTATION_CONTAINER_ROLES | _PASSIVE_ROLES) and not actions:
        return False
    return True


def _is_authoring_chrome(captured) -> bool:
    application = str(getattr(captured, "application", None) or "")
    name = str(getattr(captured, "name", None) or "")
    return application.startswith("Automation Harness") or name == "Stop Recording"


class AtspiRecordingAdapter:
    """Translate bounded AT-SPI events into the framework-neutral observation stream."""

    def __init__(self, driver=None, *, on_resolved=None, acknowledgement_seconds=0.3) -> None:
        self.driver = driver or AtspiDriver()
        self.on_resolved = on_resolved
        self.acknowledgement_seconds = acknowledgement_seconds
        self._emit: Callable[[Observation], None] | None = None
        self._lease: AtspiRegistryLease | None = None
        self._pyatspi = None
        self._listeners: list[tuple[Callable[[Any], None], str]] = []
        self._callback_condition = threading.Condition()
        self._callbacks_accepting = False
        self._active_callbacks = 0
        self._pointer_worker = _PointerRecordingWorker(
            self._publish,
            acknowledge=on_resolved,
            acknowledgement_seconds=acknowledgement_seconds,
        )

    @property
    def available(self) -> bool:
        return bool(self.driver.available)

    def start(self, emit: Callable[[Observation], None]) -> None:
        if self._lease is not None:
            raise RuntimeError("AT-SPI recording adapter is already active")
        self._emit = emit
        self._pyatspi = _pyatspi()
        self._pointer_worker.start()
        with self._callback_condition:
            self._callbacks_accepting = True
        self._listeners = [
            (self._pointer, "mouse:button:1p"),
            (self._pointer, "mouse:button:1r"),
            (self._pointer, "mouse:button:3p"),
            (self._pointer, "mouse:button:3r"),
            (self._action, "object:state-changed:checked"),
            (self._action, "object:state-changed:selected"),
            (self._text, "object:text-changed"),
        ]
        try:
            for callback, event_type in self._listeners:
                self._pyatspi.Registry.registerEventListener(callback, event_type)
            self._lease = acquire_atspi_registry(self._pyatspi)
        except Exception:
            with self._callback_condition:
                self._callbacks_accepting = False
            for callback, event_type in self._listeners:
                try:
                    self._pyatspi.Registry.deregisterEventListener(callback, event_type)
                except Exception:
                    pass
            self._listeners = []
            self._pointer_worker.stop_and_drain()
            self._emit = None
            raise

    def stop(self) -> None:
        if self._lease is None:
            return
        lease = self._lease
        self._lease = None
        with self._callback_condition:
            self._callbacks_accepting = False
        deferred_release = False
        try:
            for callback, event_type in self._listeners:
                self._pyatspi.Registry.deregisterEventListener(callback, event_type)
            with self._callback_condition:
                deferred_release = bool(self._active_callbacks)
        finally:
            self._pointer_worker.stop_and_drain()
            if deferred_release:
                threading.Thread(
                    target=self._release_after_callbacks,
                    args=(lease,),
                    name="atspi-recording-release",
                    daemon=True,
                ).start()
            else:
                lease.close()
            self._listeners = []
            self._emit = None

    def _release_after_callbacks(self, lease) -> None:
        with self._callback_condition:
            while self._active_callbacks:
                self._callback_condition.wait()
        lease.close()

    def _target(self, event: Any, coordinates=None, *, prefer_coordinates=False):
        source = getattr(event, "source", None)
        if prefer_coordinates and coordinates is not None:
            try:
                captured = self.driver.capture_scoped_at_point(*coordinates)
                return captured if _is_recordable_target(captured) else None
            except Exception:
                pass
        if source is not None and hasattr(self.driver, "capture_event_source"):
            try:
                captured = self.driver.capture_event_source(source, settle_delay=0.0)
                return captured if _is_recordable_target(captured) else None
            except Exception:
                pass
        if coordinates is not None:
            try:
                captured = self.driver.capture_scoped_at_point(*coordinates)
                return captured if _is_recordable_target(captured) else None
            except Exception:
                # Some AT-SPI implementations omit useful device coordinates.
                # Fall back only when the event source is itself a semantic
                # component rather than an application/window container.
                pass
        if source is None:
            return None
        try:
            desktop = self._pyatspi.Registry.getDesktop(0)
            captured = _capture_semantic_accessible(source, desktop, self._pyatspi)
            return captured if _is_recordable_target(captured) else None
        except Exception:
            return None

    def _pointer(self, event: Any) -> None:
        if not self._begin_callback():
            return
        try:
            self._handle_pointer(event)
        finally:
            self._end_callback()

    def _handle_pointer(self, event: Any) -> None:
        event_type = str(getattr(event, "type", "")).casefold()
        if not event_type.endswith(("1p", "1r", "3p", "3r")):
            return
        coordinates = _event_coordinates(event)
        target = None
        # Resolve on the Registry dispatch context, but use the bounded snapshot
        # pipeline rather than the full desktop-wide authoring search. Only the
        # immutable CapturedComponent crosses to the worker.
        if event_type.endswith(("1p", "3p")):
            target = self._resolve_pointer_event(event, coordinates)
        self._pointer_worker.accept_pointer(
            event_type,
            coordinates,
            time.monotonic(),
            target,
        )

    def _begin_callback(self):
        with self._callback_condition:
            if not self._callbacks_accepting:
                return False
            self._active_callbacks += 1
            return True

    def _end_callback(self):
        with self._callback_condition:
            self._active_callbacks -= 1
            if not self._active_callbacks:
                self._callback_condition.notify_all()

    def _resolve_pointer_target(self, coordinates):
        if coordinates is None:
            return None
        try:
            captured = self.driver.capture_scoped_at_point(*coordinates)
        except Exception:
            return None
        return captured if _is_recordable_target(captured) else None

    def _resolve_pointer_event(self, event, coordinates):
        source = getattr(event, "source", None)
        canonical = getattr(self.driver, "capture_click_snapshot", None)
        if canonical is not None:
            try:
                return canonical(
                    source,
                    coordinates,
                    excluded_application_prefixes=("Automation Harness",),
                )
            except Exception:
                return None
        if source is not None:
            try:
                snapshot = getattr(self.driver, "capture_event_source_snapshot", None)
                captured = snapshot(source) if snapshot is not None else self.driver.capture_event_source(
                    source, settle_delay=0.0,
                )
                if _is_authoring_chrome(captured):
                    return None
                if _is_recordable_target(captured):
                    return captured
            except Exception:
                pass
        if coordinates is None:
            return None
        try:
            snapshot = getattr(self.driver, "capture_at_point_snapshot", None)
            captured = snapshot(*coordinates) if snapshot is not None else self.driver.capture_scoped_at_point(
                *coordinates,
            )
        except Exception:
            return None
        return captured if _is_recordable_target(captured) else None

    def _action(self, event: Any) -> None:
        event_type = str(getattr(event, "type", ""))
        property_name = event_type.rsplit(":", 1)[-1]
        selected = bool(getattr(event, "detail1", True))
        self._pointer_worker.accept_action(
            property_name, selected, event_type, time.monotonic(),
        )

    def _text(self, event: Any) -> None:
        # event.source is a thread-bound PyGObject proxy. Never retain or
        # traverse it here; correlate primitive change data with the last
        # semantic target on the worker.
        after = getattr(event, "any_data", None)
        if after is not None:
            self._pointer_worker.accept_text(
                str(after), str(getattr(event, "type", "")), time.monotonic(),
            )

    def _publish(self, observation: Observation) -> None:
        if self._emit is not None:
            self._emit(observation)


def _event_coordinates(event: Any):
    """Return AT-SPI device-event desktop coordinates when supplied."""
    x = getattr(event, "detail1", None)
    y = getattr(event, "detail2", None)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (x, y)):
        return None
    if x < 0 or y < 0:
        return None
    return (int(x), int(y))
