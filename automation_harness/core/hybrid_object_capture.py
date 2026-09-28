from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any, Mapping

from automation_harness.core.object_capture import LocatorAssessment, ObjectCaptureService, _criteria_stability
from automation_harness.core.object_hierarchy import hierarchy_contract
from automation_harness.core.menu_inventory import with_inventory_metadata
from automation_harness.core.solipsys_identity import locator_is_complete, strategy_parts, visible_identity_status
from automation_harness.core.capture_boundaries import annotate_capture_boundary, classify_capture_boundary
from automation_harness.core.routed_capture import RoutedCaptureResult, RoutedCaptureService
from automation_harness.core.technology_router import AdapterKind, TargetContext
from automation_harness.core.test_object_factory import TestObjectFactory, TestObjectProposal
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.drivers.java_agent import JavaAgentDriver
from automation_harness.recording.x11_pointer import X11PointerMonitor
from automation_harness.models.component import AtspiIdentification, CapturedComponent, ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType


class HybridObjectCaptureService(ObjectCaptureService):
    """Object Capture service that races native desktop and JavaFX discovery.

    Swing/native GTK targets continue to use AT-SPI. Instrumented JavaFX JVMs
    use the native JavaFX bridge because Linux OpenJFX does not publish the
    scene graph through AT-SPI. A click on an empty JavaFX AT-SPI frame may
    therefore fail on the AT-SPI worker while the JavaFX worker still succeeds;
    failures are not considered terminal until every active capture backend has
    returned.
    """

    def __init__(self, driver=None, javafx_driver=None, java_agent_driver=None, pointer_monitor_factory=None) -> None:
        super().__init__(driver=driver)
        self.javafx_driver = javafx_driver or JavaFxBridgeDriver()
        self.java_agent_driver = java_agent_driver or JavaAgentDriver()
        self.pointer_monitor_factory = pointer_monitor_factory or X11PointerMonitor
        self._log(
            "capture_backends_ready",
            atspi_available=bool(getattr(self.driver, "available", False)),
            javafx_bridge_available=self._javafx_available(),
            javafx_bridge_pids=self._javafx_pids(),
            java_agent_available=bool(self.java_agent_driver.available),
        )

    @property
    def available(self) -> bool:
        return bool(getattr(self.driver, "available", False)) or self._javafx_available() or bool(self.java_agent_driver.available)

    def observe_next_click(
        self,
        target: TargetContext,
        *,
        timeout: float = 30.0,
    ) -> RoutedCaptureResult:
        """Capture through the deterministic object-lifecycle pipeline.

        Callers must first correlate the pointer target to a window and PID.
        The legacy ``capture_next_click`` entry point remains available while
        authoring surfaces migrate, but new capture and recording paths should
        use this method so only the routed adapter observes the input.
        """
        adapters = {
            AdapterKind.ATSPI: self.driver,
            AdapterKind.JAVAFX: self.javafx_driver,
            AdapterKind.JAVA_AGENT: self.java_agent_driver,
            # Rendered surfaces are discovered and operated by the mixed Java
            # agent, but remain a separate routing classification.
            AdapterKind.RENDERED: self.java_agent_driver,
        }
        return RoutedCaptureService(adapters).capture(target, timeout=timeout)

    def definition_from_observation(
        self,
        observation,
        *,
        display_name: str,
        owner_object_id: str | None = None,
        ordinal: int | None = None,
    ) -> TestObjectProposal:
        """Materialize a routed observation through the canonical factory."""
        return TestObjectFactory().materialize(
            observation,
            display_name=display_name,
            owner_object_id=owner_object_id,
            ordinal=ordinal,
        )

    def capture_next_click(self, *, timeout: float = 30.0, click_count: int = 1) -> CapturedComponent:
        """Capture against the topmost X11 client and its authoritative adapter.

        X11 owns window stacking and supplies the PID at the physical press.
        Backends are therefore never raced: a covered window cannot win by
        responding faster, and a JavaFX process cannot silently degrade to an
        AWT canvas or a desktop accessibility object.
        """
        if isinstance(click_count, bool) or not isinstance(click_count, int) or not 1 <= click_count <= 9:
            raise ValueError("click_count must be an integer from 1 through 9")
        if click_count != 1:
            captured = None
            for _index in range(click_count):
                captured = self.capture_next_click(timeout=timeout, click_count=1)
            assert captured is not None
            return captured
        result = queue.Queue(maxsize=1)
        monitor = self.pointer_monitor_factory()

        def pointer(event_type, coordinates, _timestamp, owner_pid=None):
            if not event_type.endswith(("1p", "3p")) or not result.empty():
                return
            # The Capture dialog and repository editor belong to this process.
            # Their clicks are not capture targets and must not terminate the
            # pending recapture or trigger a native accessibility traversal.
            if owner_pid == os.getpid():
                return
            try:
                if owner_pid is None:
                    raise LookupError("X11 did not identify the topmost client process")
                captured, backend = self._capture_owned_point(coordinates, owner_pid)
                result.put_nowait((captured, backend, None))
            except Exception as exc:
                result.put_nowait((None, None, exc))

        self._log("owned_capture_started", timeout=timeout, javafx_bridge_pids=self._javafx_pids())
        monitor.start(pointer)
        try:
            try:
                captured, backend, error = result.get(timeout=timeout)
            except queue.Empty:
                raise TimeoutError("timed out waiting for a click on an application window")
        finally:
            monitor.stop()
        if error is not None:
            self._log("owned_capture_failed", error_type=type(error).__name__, error=str(error))
            raise error
        self._log("owned_capture_succeeded", backend=backend, capture=captured.to_dict())
        return self._annotated(captured, backend=backend)

    def _capture_owned_point(self, coordinates, owner_pid):
        javafx_pids = set(self._javafx_pids())
        if owner_pid in javafx_pids:
            captured = self.javafx_driver.capture_at_point(*coordinates, process_id=owner_pid)
            if _capture_process_id(captured) != owner_pid:
                raise LookupError("JavaFX bridge returned an object from the wrong process")
            return captured, "javafx"

        if bool(self.java_agent_driver.available):
            try:
                captured = self.java_agent_driver.capture_at_point(*coordinates, process_id=owner_pid)
            except Exception as exc:
                raise LookupError("authoritative Java agent could not resolve the X11 owner: %s" % exc) from exc
            if _capture_process_id(captured) != owner_pid:
                raise LookupError("Java agent returned an object from the wrong process")
            return captured, "java-agent"

        snapshot = getattr(self.driver, "capture_at_point_snapshot", None)
        captured = snapshot(*coordinates) if snapshot is not None else self.driver.capture_scoped_at_point(*coordinates)
        if _capture_process_id(captured) != owner_pid:
            raise LookupError("accessibility returned an object from a covered or unrelated process")
        return captured, "atspi"

    def assess(self, captured: CapturedComponent) -> tuple[LocatorAssessment, ...]:
        strategy = captured.candidate_strategy()
        if strategy.type != "javafx":
            return super().assess(captured)
        identification = strategy.options.get("identification")
        if not isinstance(identification, Mapping):
            raise ValueError("captured JavaFX object has no identification mapping")
        stages = self.javafx_driver.assess_identification(
            identification,
            process_id=_capture_process_id(captured),
        )
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
        validate_live: bool = True,
    ) -> ComponentDefinition:
        authored = captured.candidate_strategy()
        native_class = str(captured.native_class or "").casefold()
        if "menubuttonskin" in native_class or "menuitemcontainer" in native_class:
            raise ValueError(
                "JavaFX menu skin captures are transient; capture the logical "
                "MenuButton/Menu owner instead"
            )
        if authored.type not in {"javafx", "java_agent"}:
            return super().definition_from_capture(
                component_id,
                captured,
                description=description,
                criteria=criteria,
                identification=identification,
                revision=revision,
                validate_live=validate_live,
            )
        if authored.type == "java_agent":
            if criteria is not None and identification is not None:
                raise ValueError("supply criteria or identification, not both")
            raw_identity = identification or ({"mandatory": dict(criteria)} if criteria is not None else authored.options.get("identification"))
            if not isinstance(raw_identity, Mapping) or not isinstance(raw_identity.get("mandatory"), Mapping) or not raw_identity.get("mandatory"):
                raise ValueError("captured Java agent object requires mandatory identity evidence")
            if captured.framework == "solipsys_rendered":
                mandatory, _assistive = strategy_parts({"identification": raw_identity})
                if not locator_is_complete(mandatory):
                    raise ValueError(
                        "captured Solipsys object has no validated durable identity; "
                        "runtime references and field:identity cannot be persisted"
                    )
                matches = captured.backend_properties.get("identity_visible_match_count")
                if visible_identity_status(captured.backend_properties) == "ambiguous":
                    raise ValueError(
                        "captured Solipsys identity is ambiguous in the visible surface scope: "
                        "%s rendered objects match" % matches
                    )
            actions = {"resolve"}
            action_names = {str(value).casefold() for value in captured.actions}
            if action_names & {"click", "press", "activate"}:
                actions.add("activate")
            if "focus" in action_names:
                actions.add("focus")
            if "set_text" in action_names:
                actions.update({"set_text", "clear_text", "append_text"})
            for action_name in (
                "select_item",
                "select_row",
                "select_cell",
                "set_value",
            ):
                if action_name in action_names:
                    actions.add(action_name)
            if (
                "select_menu_item" in action_names
                or (
                    captured.semantic_type() in {
                        ObjectType.MENU_BAR,
                        ObjectType.MENU,
                        ObjectType.CONTEXT_MENU,
                    }
                )
            ):
                actions.add(ActionType.SELECT_MENU_ITEM.value)
            return ComponentDefinition(
                component_id=component_id,
                description=description or captured.description or captured.name or "Captured native Java object",
                strategies=(ComponentStrategy("java_agent", {"identification": dict(raw_identity)}),),
                actions=frozenset(actions), revision=revision,
                object_type=captured.semantic_type(),
                properties=with_inventory_metadata(
                    captured.backend_properties,
                    captured.semantic_type(),
                    captured.logical_subobjects,
                    complete=True,
                    source="native_java_model",
                ),
                framework=captured.framework, native_class=captured.native_class,
                subobjects={
                    str(key): dict(value)
                    for key, value in captured.logical_subobjects.items()
                },
                scope=hierarchy_contract(captured),
            )
        if criteria is not None and identification is not None:
            raise ValueError("supply criteria or identification, not both")

        identity = self._javafx_identity(authored, criteria=criteria, identification=identification)
        mandatory = identity.get("mandatory")
        if not isinstance(mandatory, Mapping) or not mandatory:
            raise ValueError("captured JavaFX object requires at least one mandatory identification condition")

        if validate_live and self._javafx_available():
            stages = self.javafx_driver.assess_identification(
                identity,
                process_id=_capture_process_id(captured),
            )
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
        if captured.semantic_type() in {
            ObjectType.MENU_BAR, ObjectType.MENU, ObjectType.CONTEXT_MENU,
        }:
            actions.add(ActionType.SELECT_MENU_ITEM.value)

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
            properties=with_inventory_metadata(
                captured.backend_properties,
                captured.semantic_type(),
                captured.logical_subobjects,
                complete=True,
                source="javafx_model",
            ),
            framework="javafx",
            native_class=captured.native_class,
            subobjects=captured.logical_subobjects,
            scope=hierarchy_contract(captured),
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
        return self._annotated(captured, backend="javafx")

    def _annotated(self, captured: CapturedComponent, *, backend: str) -> CapturedComponent:
        captured = annotate_capture_boundary(captured)
        boundary = classify_capture_boundary(captured)
        self._log(
            "capture_framework_classified",
            backend=backend,
            application=captured.application,
            window=captured.window,
            pointer=dict(captured.backend_properties or {}).get("capture_point"),
            native_class=captured.native_class,
            component_bounds=list(captured.bounds) if captured.bounds else None,
            boundary=boundary.to_dict(),
            javafx_evidence=boundary.framework == "javafx",
            jogl_evidence=boundary.framework == "jogl",
            final_candidate=captured.to_dict(),
        )
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


def _capture_process_id(captured):
    properties = dict(captured.backend_properties or {})
    raw = properties.get("bridge_pid", properties.get("process_id", properties.get("pid")))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


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
