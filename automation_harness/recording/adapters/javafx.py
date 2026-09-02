"""JavaFX agent event adapter with source-level noise filtering."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Mapping

from automation_harness.drivers.javafx_bridge import JavaFxRecordingBridge, JavaFxRecordingTransport
from automation_harness.recording.observations import ActionFired, FocusChanged, Observation, PointerInteraction, StateChanged, TextChanged


class JavaFxRecordingAdapter:
    """Consumes bounded event batches emitted by the in-process JavaFX agent."""

    _IGNORED = frozenset({"mouse_moved", "hover", "layout", "css", "bounds", "skin", "pressed"})

    def __init__(self, transport: JavaFxRecordingTransport, *, read_timeout: float = 0.25) -> None:
        self.transport = transport
        self.driver = JavaFxRecordingBridge(transport)
        self.read_timeout = read_timeout
        self._emit: Callable[[Observation], None] | None = None
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, emit: Callable[[Observation], None]) -> None:
        if self._thread is not None:
            raise RuntimeError("JavaFX recording adapter is already active")
        self._emit = emit
        self._stopping.clear()
        self.transport.request("record_start", {})
        self._thread = threading.Thread(target=self._read_loop, name="javafx-recording", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self.read_timeout * 4))
            self._thread = None
        response = self.transport.request("record_stop", {})
        self._emit_events(response.get("observations", ()))
        self._emit = None

    def _read_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                response = self.transport.request("record_read", {"timeout": self.read_timeout})
                self._emit_events(response.get("observations", ()))
            except Exception:
                # The session will stop and surface transport failures through
                # its adapter lifecycle; do not spin on a failed endpoint.
                self._stopping.set()

    def _emit_events(self, values: Any) -> None:
        if not isinstance(values, (list, tuple)) or self._emit is None:
            return
        for value in values:
            if isinstance(value, Mapping):
                observation = self.normalize(value)
                if observation is not None:
                    self._emit(observation)

    def normalize(self, event: Mapping[str, Any]) -> Observation | None:
        kind = str(event.get("type", "")).casefold()
        if kind in self._IGNORED:
            return None
        timestamp = float(event.get("timestamp", time.monotonic()))
        target = None
        if isinstance(event.get("target"), Mapping):
            target = self.driver.semantic_target(event["target"]).capture()
        evidence = event.get("evidence", {})
        if not isinstance(evidence, Mapping):
            evidence = {}
        if kind == "pointer":
            point = event.get("coordinates")
            coordinates = tuple(int(value) for value in point) if isinstance(point, (list, tuple)) and len(point) == 2 else None
            return PointerInteraction(timestamp, "javafx", target, dict(evidence), str(event.get("button", "primary")), str(event.get("phase", "released")), coordinates)
        if kind == "action":
            return ActionFired(timestamp, "javafx", target, dict(evidence), str(event.get("action", "activate")))
        if kind == "text_changed":
            return TextChanged(timestamp, "javafx", target, dict(evidence), event.get("before"), event.get("after"))
        if kind == "state_changed":
            property_name = str(event.get("property", ""))
            if property_name in self._IGNORED:
                return None
            return StateChanged(timestamp, "javafx", target, dict(evidence), property_name, event.get("before"), event.get("after"))
        if kind == "focus":
            return FocusChanged(timestamp, "javafx", target, dict(evidence), bool(event.get("focused", True)))
        return None
