from __future__ import annotations

import queue
import threading
import time
from typing import Any, Mapping

from automation_harness.core.object_capture import LocatorAssessment, ObjectCaptureService, _criteria_stability
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.models.component import AtspiIdentification, CapturedComponent, ComponentDefinition, ComponentStrategy


class HybridObjectCaptureService(ObjectCaptureService):
    """Object Capture service that races native desktop and JavaFX discovery.

    Swing/native GTK targets continue to use AT-SPI. Instrumented JavaFX JVMs
    use the native JavaFX bridge because Linux OpenJFX does not publish the
    scene graph through AT-SPI. A click on an empty JavaFX AT-SPI frame may
    therefore fail on the AT-SPI worker while the JavaFX worker still succeeds;
    failures are not considered terminal until every active capture backend has
    returned.
    """

    def __init__(self, driver=None, javafx_driver=None) -> None:
        super().__init__(driver=driver)
        self.javafx_driver = javafx_driver or JavaFxBridgeDriver()
        self._log(
            "capture_backends_ready",
            atspi_available=bool(getattr(self.driver, "available", False)),
            javafx_bridge_available=self._javafx_available(),
            javafx_bridge_pids=self._javafx_pids(),
        )

    @property
    def available(self) -> bool:
        return bool(getattr(self.driver, "available", False)) or self._javafx_available()

    def capture_next_click(self, *, timeout: float = 30.0) -> CapturedComponent:
        """Return the first successful next-click capture from any live backend."""
        atspi_available = bool(getattr(self.driver, "available", False))
        javafx_available = self._javafx_available()
        self._log(
            "hybrid_capture_next_click_started",
            timeout=timeout,
            atspi_available=atspi_available,
            javafx_bridge_available=javafx_available,
            javafx_bridge_pids=self._javafx_pids(),
        )

        if not javafx_available:
            return super().capture_next_click(timeout=timeout)
        if not atspi_available:
            return self._capture_javafx_next_click(timeout)

        results = queue.Queue()
        backends = (
            ("atspi", self.driver),
            ("javafx", self.javafx_driver),
        )

        def worker(name, backend):
            try:
                captured = backend.capture_next_click(timeout=timeout)
            except Exception as exc:
                results.put((name, None, exc))
            else:
                results.put((name, captured, None))

        for name, backend in backends:
            thread = threading.Thread(
                target=worker,
                args=(name, backend),
                name="automation-%s-click-capture" % name,
                daemon=True,
            )
            thread.start()

        errors = []
        deadline = time.monotonic() + timeout + 2.5
        remaining = len(backends)
        while remaining:
            wait = max(0.01, deadline - time.monotonic())
            if wait <= 0:
                break
            try:
                name, captured, error = results.get(timeout=wait)
            except queue.Empty:
                break
            remaining -= 1
            if captured is not None:
                self._log(
                    "hybrid_capture_next_click_succeeded",
                    backend=name,
                    capture=captured.to_dict(),
                )
                return captured
            errors.append("%s: %s: %s" % (name, type(error).__name__, error))
            self._log(
                "hybrid_capture_backend_failed",
                backend=name,
                error_type=type(error).__name__,
                error=str(error),
            )

        message = "no capture backend resolved the selected object"
        if errors:
            message += "; " + "; ".join(errors)
        self._log("hybrid_capture_next_click_failed", error=message)
        raise LookupError(message)

    def assess(self, captured: CapturedComponent) -> tuple[LocatorAssessment, ...]:
        strategy = captured.candidate_strategy()
        if strategy.type != "javafx":
            return super().assess(captured)
        identification = strategy.options.get("identification")
        if not isinstance(identification, Mapping):
            raise ValueError("captured JavaFX object has no identification mapping")
        stages = self.javafx_driver.assess_identification(identification)
        return tuple(
            LocatorAssessment(
                source=stage.source,
                criteria=dict(stage.criteria),
                matches=stage.matches,
                stability=_javafx_criteria_stability(stage.criteria),
            )
            for stage in stages
        )

    def definition_from_capture(
        self,
        component_id: str,
        captured: CapturedComponent,
        *,
        description: str = "",
        criteria: Mapping[str, Any] | None = None,
        identification: AtspiIdentification | Mapping[str, Any] | None = None,
        revision: int = 1,
    ) -> ComponentDefinition:
        authored = captured.candidate_strategy()
        if authored.type != "javafx":
            return super().definition_from_capture(
                component_id,
                captured,
                description=description,
                criteria=criteria,
                identification=identification,
                revision=revision,
            )
        if criteria is not None and identification is not None:
            raise ValueError("supply criteria or identification, not both")

        identity = self._javafx_identity(authored, criteria=criteria, identification=identification)
        mandatory = identity.get("mandatory")
        if not isinstance(mandatory, Mapping) or not mandatory:
            raise ValueError("captured JavaFX object requires at least one mandatory identification condition")

        if self._javafx_available():
            stages = self.javafx_driver.assess_identification(identity)
            if not stages or stages[-1].matches == 0:
                raise ValueError("authored JavaFX identity does not resolve the captured object")
            remaining = stages[-1].matches
            ordinal = identity.get("ordinal")
            if remaining > 1 and ordinal is None:
                raise ValueError(
                    "authored JavaFX identity remains ambiguous: %s runtime objects match; "
                    "add assistive conditions or an explicit ordinal" % remaining
                )
            if ordinal is not None:
                if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
                    raise ValueError("JavaFX identification ordinal must be a non-negative integer")
                if ordinal >= remaining:
                    raise ValueError(
                        "authored JavaFX ordinal %s is outside %s matching candidates" % (ordinal, remaining)
                    )

        actions = {"resolve"}
        action_names = {str(value).casefold() for value in captured.actions}
        if action_names & {"click", "press", "activate"}:
            actions.add("activate")
        if "set_text" in action_names:
            actions.update({"set_text", "clear_text", "append_text"})

        expected = {
            key: value
            for key, value in captured.state.to_dict().items()
            if key in {"visible", "showing", "enabled"} and value is not None
        }
        return ComponentDefinition(
            component_id=component_id,
            description=description or captured.description or captured.name or "Captured JavaFX object",
            strategies=(ComponentStrategy("javafx", {"identification": identity}),),
            actions=frozenset(actions),
            expected_states=expected,
            revision=revision,
            object_type=captured.semantic_type(),
            properties=dict(captured.backend_properties),
            framework="javafx",
            native_class=captured.native_class,
            subobjects=captured.logical_subobjects,
        )

    def _capture_javafx_next_click(self, timeout: float) -> CapturedComponent:
        try:
            captured = self.javafx_driver.capture_next_click(timeout=timeout)
        except Exception as exc:
            self._log(
                "javafx_capture_next_click_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        self._log("javafx_capture_next_click_succeeded", capture=captured.to_dict())
        return captured

    @staticmethod
    def _javafx_identity(authored, *, criteria, identification):
        if criteria is not None:
            return {"mandatory": dict(criteria)}
        if identification is None:
            raw = authored.options.get("identification")
            if not isinstance(raw, Mapping):
                raise ValueError("captured JavaFX strategy has no identification mapping")
            return _normalize_javafx_identity(raw)
        if isinstance(identification, AtspiIdentification):
            return _normalize_javafx_identity(identification.to_dict())
        return _normalize_javafx_identity(identification)

    def _javafx_available(self) -> bool:
        try:
            return bool(self.javafx_driver.available)
        except Exception:
            return False

    def _javafx_pids(self):
        try:
            return [endpoint.pid for endpoint in self.javafx_driver.endpoints()]
        except Exception:
            return []


def _normalize_javafx_identity(value: Mapping[str, Any]) -> dict[str, Any]:
    raw = dict(value)
    mandatory = raw.get("mandatory", {})
    assistive = raw.get("assistive", {})
    ordinal = raw.get("ordinal")
    if not isinstance(mandatory, Mapping) or not isinstance(assistive, Mapping):
        raise ValueError("JavaFX identification mandatory/assistive values must be mappings")
    result = {"mandatory": dict(mandatory)}
    if assistive:
        result["assistive"] = dict(assistive)
    if ordinal is not None:
        if isinstance(ordinal, Mapping):
            ordinal = ordinal.get("index")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise ValueError("JavaFX identification ordinal must be a non-negative integer")
        result["ordinal"] = ordinal
    return result


def _javafx_criteria_stability(criteria: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in criteria.items():
        if key in {"parent", "properties", "layout", "ancestor"} and isinstance(value, Mapping):
            result[key] = {child: _javafx_stability(key, child) for child in value}
        elif key == "lineage" and isinstance(value, (list, tuple)):
            result[key] = "high"
        else:
            result[key] = _javafx_stability(key)
    return result


def _javafx_stability(key: str, child: str | None = None) -> str:
    if key == "properties":
        folded = (child or "").casefold()
        if folded.startswith(("automation.", "test.", "qa.")):
            return "very-high"
        return "medium"
    if key == "layout":
        return "high" if (child or "").startswith("grid_") else "medium"
    return {
        "id": "very-high",
        "user_data": "high",
        "window": "high",
        "accessible_text": "high",
        "accessible_role": "high",
        "text": "high",
        "parent": "high",
        "ancestor": "high",
        "lineage": "high",
        "class": "medium-high",
        "style_classes": "medium",
        "hierarchy": "medium-low",
        "sibling_index": "low",
    }.get(key, "unknown")
