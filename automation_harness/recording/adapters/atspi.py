"""AT-SPI event adapter for recording native GTK and Swing interactions."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

from automation_harness.drivers.atspi_driver import _capture_accessible, _pyatspi
from automation_harness.recording.observations import ActionFired, Observation, PointerInteraction, StateChanged, TextChanged


class AtspiRecordingAdapter:
    """Translate bounded AT-SPI events into the framework-neutral observation stream."""

    def __init__(self) -> None:
        self._emit: Callable[[Observation], None] | None = None
        self._thread: threading.Thread | None = None
        self._pyatspi = None
        self._listeners: list[tuple[Callable[[Any], None], str]] = []
        self._started = threading.Event()

    @property
    def available(self) -> bool:
        try:
            _pyatspi()
        except Exception:
            return False
        return True

    def start(self, emit: Callable[[Observation], None]) -> None:
        if self._thread is not None:
            raise RuntimeError("AT-SPI recording adapter is already active")
        self._emit = emit
        self._pyatspi = _pyatspi()
        self._listeners = [
            (self._pointer, "mouse:button:1r"),
            (self._pointer, "mouse:button:3r"),
            (self._action, "object:state-changed:checked"),
            (self._action, "object:state-changed:selected"),
            (self._text, "object:text-changed"),
        ]
        for callback, event_type in self._listeners:
            self._pyatspi.Registry.registerEventListener(callback, event_type)
        self._thread = threading.Thread(target=self._run, name="atspi-recording", daemon=True)
        self._thread.start()
        self._started.wait(timeout=1.0)

    def stop(self) -> None:
        if self._thread is None:
            return
        try:
            for callback, event_type in self._listeners:
                self._pyatspi.Registry.deregisterEventListener(callback, event_type)
            self._pyatspi.Registry.stop()
        finally:
            self._thread.join(timeout=2.0)
            self._thread = None
            self._listeners = []
            self._emit = None

    def _run(self) -> None:
        self._started.set()
        self._pyatspi.Registry.start()

    def _target(self, event: Any):
        source = getattr(event, "source", None)
        if source is None:
            return None
        try:
            captured = _capture_accessible(source, self._pyatspi)
            if (captured.application or "").startswith("Automation Harness") or captured.name == "Stop Recording":
                return None
            return captured
        except Exception:
            return None

    def _pointer(self, event: Any) -> None:
        event_type = str(getattr(event, "type", "")).casefold()
        if not event_type.endswith(("1r", "3r")):
            return
        target = self._target(event)
        if target is None:
            return
        self._publish(PointerInteraction(
            time.monotonic(), "atspi", target, {"event_type": event_type},
            "secondary" if event_type.endswith("3r") else "primary", "released", None,
        ))

    def _action(self, event: Any) -> None:
        target = self._target(event)
        if target is None:
            return
        event_type = str(getattr(event, "type", ""))
        property_name = event_type.rsplit(":", 1)[-1]
        selected = bool(getattr(event, "detail1", True))
        self._publish(StateChanged(
            time.monotonic(), "atspi", target, {"event_type": event_type},
            property_name, not selected, selected,
        ))
        self._publish(ActionFired(
            time.monotonic(), "atspi", target, {"event_type": event_type},
            "toggle" if property_name == "checked" else "select",
        ))

    def _text(self, event: Any) -> None:
        target = self._target(event)
        if target is None:
            return
        after = None
        try:
            text = getattr(event.source, "queryText")()
            after = text.getText(0, text.characterCount)
        except Exception:
            after = getattr(event, "any_data", None)
        if after is not None:
            self._publish(TextChanged(
                time.monotonic(), "atspi", target,
                {"event_type": str(getattr(event, "type", ""))}, None, str(after),
            ))

    def _publish(self, observation: Observation) -> None:
        if self._emit is not None:
            self._emit(observation)
