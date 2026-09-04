"""AT-SPI event adapter for recording native GTK and Swing interactions."""
from __future__ import annotations

import time
from typing import Any, Callable

from automation_harness.drivers.atspi_driver import AtspiDriver, _capture_accessible, _pyatspi
from automation_harness.drivers.atspi_registry import AtspiRegistryLease, acquire_atspi_registry
from automation_harness.recording.observations import ActionFired, Observation, PointerInteraction, StateChanged, TextChanged


class AtspiRecordingAdapter:
    """Translate bounded AT-SPI events into the framework-neutral observation stream."""

    def __init__(self, driver=None) -> None:
        self.driver = driver or AtspiDriver()
        self._emit: Callable[[Observation], None] | None = None
        self._lease: AtspiRegistryLease | None = None
        self._pyatspi = None
        self._listeners: list[tuple[Callable[[Any], None], str]] = []

    @property
    def available(self) -> bool:
        return bool(self.driver.available)

    def start(self, emit: Callable[[Observation], None]) -> None:
        if self._lease is not None:
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
        try:
            for callback, event_type in self._listeners:
                self._pyatspi.Registry.registerEventListener(callback, event_type)
            self._lease = acquire_atspi_registry(self._pyatspi)
        except Exception:
            for callback, event_type in self._listeners:
                try:
                    self._pyatspi.Registry.deregisterEventListener(callback, event_type)
                except Exception:
                    pass
            self._listeners = []
            self._emit = None
            raise

    def stop(self) -> None:
        if self._lease is None:
            return
        lease = self._lease
        self._lease = None
        try:
            for callback, event_type in self._listeners:
                self._pyatspi.Registry.deregisterEventListener(callback, event_type)
        finally:
            lease.close()
            self._listeners = []
            self._emit = None

    def _target(self, event: Any, coordinates=None):
        if coordinates is not None:
            try:
                return self.driver.capture_scoped_at_point(*coordinates)
            except Exception:
                # Some AT-SPI implementations omit useful device coordinates.
                # Fall back only when the event source is itself a semantic
                # component rather than an application/window container.
                pass
        source = getattr(event, "source", None)
        if source is None:
            return None
        try:
            captured = _capture_accessible(source, self._pyatspi)
            structural_roles = {"application", "desktop", "frame", "window", "root pane"}
            if (
                (captured.application or "").startswith("Automation Harness")
                or captured.name == "Stop Recording"
                or (captured.role or "").replace("_", " ").casefold() in structural_roles
            ):
                return None
            return captured
        except Exception:
            return None

    def _pointer(self, event: Any) -> None:
        event_type = str(getattr(event, "type", "")).casefold()
        if not event_type.endswith(("1r", "3r")):
            return
        coordinates = _event_coordinates(event)
        target = self._target(event, coordinates)
        if target is None:
            return
        self._publish(PointerInteraction(
            time.monotonic(), "atspi", target, {"event_type": event_type, "coordinates": coordinates},
            "secondary" if event_type.endswith("3r") else "primary", "released", coordinates,
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


def _event_coordinates(event: Any):
    """Return AT-SPI device-event desktop coordinates when supplied."""
    x = getattr(event, "detail1", None)
    y = getattr(event, "detail2", None)
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in (x, y)):
        return None
    if x < 0 or y < 0:
        return None
    return (int(x), int(y))
