from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from automation_harness.models.component import (
    AtspiIdentification,
    CapturedComponent,
    ComponentState,
    ResolvedComponent,
)


class AtspiUnavailable(RuntimeError):
    pass


class AtspiResolutionError(LookupError):
    pass


class AtspiObjectNotFound(AtspiResolutionError):
    pass


class AtspiAmbiguousObject(AtspiResolutionError):
    pass


@dataclass(frozen=True)
class AtspiResolutionStage:
    source: str
    criteria: Mapping[str, Any]
    matches: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "criteria": dict(self.criteria),
            "matches": self.matches,
        }


@dataclass
class AtspiDriver:
    """Linux AT-SPI adapter for resolution, interaction, state and object capture.

    Object identity is progressive rather than first-match based. Mandatory
    conditions are always conjunctive. Assistive conditions are added in order
    only while multiple candidates remain. If multiple runtime objects still
    survive the complete identity, resolution fails unless an explicit ordinal
    was authored for that locator.
    """

    context: "TestContext | None" = None

    @property
    def available(self) -> bool:
        try:
            import pyatspi  # type: ignore  # noqa: F401
        except ImportError:
            return False
        return True

    def resolve(
        self,
        component_id: str,
        *,
        identification: Mapping[str, Any] | AtspiIdentification | None = None,
        name: str | None = None,
        role: str | None = None,
        accessible_id: str | None = None,
    ) -> ResolvedComponent:
        pyatspi = _pyatspi()
        locator = _identification(identification, name=name, role=role, accessible_id=accessible_id)
        match, trace = _select_accessible(pyatspi.Registry.getDesktop(0), locator)
        captured = _capture_accessible(match, pyatspi)
        metadata = captured.to_dict()
        metadata["resolution"] = {
            "identification": locator.to_dict(),
            "stages": [stage.to_dict() for stage in trace],
        }
        return ResolvedComponent(component_id=component_id, strategy="atspi", metadata=metadata)

    def count_matches(
        self,
        *,
        identification: Mapping[str, Any] | AtspiIdentification | None = None,
        name: str | None = None,
        role: str | None = None,
        accessible_id: str | None = None,
    ) -> int:
        locator = _identification(identification, name=name, role=role, accessible_id=accessible_id)
        criteria = dict(locator.mandatory)
        criteria.update(locator.assistive)
        return len(_matching_accessibles(_pyatspi().Registry.getDesktop(0), criteria))

    def assess_identification(
        self,
        identification: Mapping[str, Any] | AtspiIdentification,
    ) -> tuple[AtspiResolutionStage, ...]:
        locator = _identification(identification)
        desktop = _pyatspi().Registry.getDesktop(0)
        _, stages = _progressive_candidates(desktop, locator)
        return stages

    def inspect(
        self,
        *,
        identification: Mapping[str, Any] | AtspiIdentification | None = None,
        name: str | None = None,
        role: str | None = None,
        accessible_id: str | None = None,
    ) -> CapturedComponent:
        pyatspi = _pyatspi()
        locator = _identification(identification, name=name, role=role, accessible_id=accessible_id)
        match, _trace = _select_accessible(pyatspi.Registry.getDesktop(0), locator)
        return _capture_accessible(match, pyatspi)

    def capture_at_point(self, x: int, y: int) -> CapturedComponent:
        """Capture the deepest accessible object containing one desktop point."""
        pyatspi = _pyatspi()
        desktop = pyatspi.Registry.getDesktop(0)
        match = _deepest_at_point(desktop, x=x, y=y, pyatspi=pyatspi)
        if match is None:
            raise LookupError(f"no AT-SPI object found at desktop point ({x}, {y})")
        return _capture_accessible(match, pyatspi)

    def capture_scoped_at_point(self, x: int, y: int) -> CapturedComponent:
        """Identify the application under a point, then resolve within it.

        Re-querying the live application prevents a transient or stale proxy
        from becoming the captured component after the picker disappears.
        """
        pyatspi = _pyatspi()
        desktop = pyatspi.Registry.getDesktop(0)
        source = _deepest_at_point(desktop, x=x, y=y, pyatspi=pyatspi)
        if source is None:
            raise LookupError(f"no application matched desktop point ({x}, {y})")
        application_name = _application_name(source)
        if not application_name:
            raise LookupError(f"object at desktop point ({x}, {y}) has no application source")
        application = _live_application(desktop, application_name)
        if application is None:
            raise LookupError(f"application {application_name!r} is no longer live")
        target = _deepest_at_point(application, x=x, y=y, pyatspi=pyatspi)
        if target is None:
            raise LookupError(
                f"no live component matched desktop point ({x}, {y}) "
                f"within application {application_name!r}"
            )
        return _capture_accessible(target, pyatspi)

    def capture_next_click(self, *, timeout: float = 30.0) -> CapturedComponent:
        """Wait for one desktop mouse press and capture its accessible source.

        AT-SPI dispatches global ``mouse:button:1p`` events on X11.  Wayland
        sessions may withhold global mouse events, so a focused-state event is
        registered as a fallback.  A normal click that targets an interactive
        object moves focus to it; in both cases the event source is the object
        to capture.  This avoids relying on a delayed pointer sample or
        accidentally capturing the authoring window itself.
        """
        if timeout <= 0:
            raise ValueError("click capture timeout must be positive")
        pyatspi = _pyatspi()
        baseline_focus = _focused_accessible(pyatspi.Registry.getDesktop(0), pyatspi)
        baseline_signature = _accessible_signature(baseline_focus, pyatspi) if baseline_focus is not None else None
        outcome: queue.Queue[tuple[CapturedComponent | None, BaseException | None]] = queue.Queue(maxsize=1)
        completed = threading.Event()

        def finish(captured: CapturedComponent | None, error: BaseException | None) -> None:
            if completed.is_set():
                return
            completed.set()
            outcome.put((captured, error))
            try:
                pyatspi.Registry.stop()
            except Exception:
                pass

        def on_target_event(event: Any) -> None:
            event_type = str(getattr(event, "type", ""))
            if event_type == "object:state-changed:focused" and not bool(getattr(event, "detail1", False)):
                return
            source = getattr(event, "source", None)
            if source is None:
                return
            if _application_name(source) == "Automation Harness Object Capture":
                return
            if baseline_signature is not None and _accessible_signature(source, pyatspi) == baseline_signature:
                # The focused object that was already active when capture was
                # armed is not a new user selection.
                return
            try:
                # Treat one click as a scoped operation. First determine its
                # application source, then resolve the deepest *live* object
                # at the source bounds within that application only.
                source_capture = _capture_accessible(source, pyatspi)
                if source_capture.application is None or source_capture.bounds is None:
                    raise LookupError("clicked accessibility event has no application-scoped component bounds")
                left, top, width, height = source_capture.bounds
                time.sleep(0.08)
                application = _live_application(
                    pyatspi.Registry.getDesktop(0), source_capture.application,
                )
                if application is None:
                    raise LookupError(f"clicked application {source_capture.application!r} is no longer live")
                target = _deepest_at_point(
                    application,
                    x=left + width // 2,
                    y=top + height // 2,
                    pyatspi=pyatspi,
                )
                if target is None:
                    raise LookupError("no live component matched the clicked point within its application")
                captured = _capture_accessible(target, pyatspi)
                finish(captured, None)
            except BaseException as exc:
                finish(None, exc)

        event_types = ("mouse:button:1p", "object:state-changed:focused")
        for event_type in event_types:
            pyatspi.Registry.registerEventListener(on_target_event, event_type)
        timer = threading.Timer(timeout, lambda: finish(None, TimeoutError("no object was clicked before capture timed out")))
        timer.daemon = True
        timer.start()
        try:
            pyatspi.Registry.start()
        finally:
            timer.cancel()
            try:
                for event_type in event_types:
                    pyatspi.Registry.deregisterEventListener(on_target_event, event_type)
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

    def state(
        self,
        *,
        identification: Mapping[str, Any] | AtspiIdentification | None = None,
        name: str | None = None,
        role: str | None = None,
        accessible_id: str | None = None,
    ) -> ComponentState:
        pyatspi = _pyatspi()
        locator = _identification(identification, name=name, role=role, accessible_id=accessible_id)
        try:
            match, _trace = _select_accessible(pyatspi.Registry.getDesktop(0), locator)
        except AtspiObjectNotFound:
            return ComponentState(
                present=False,
                properties={"identification": locator.to_dict()},
            )
        return _capture_accessible(match, pyatspi).state

    def activate(
        self,
        *,
        identification: Mapping[str, Any] | AtspiIdentification | None = None,
        name: str | None = None,
        role: str | None = None,
        accessible_id: str | None = None,
    ) -> dict[str, Any]:
        pyatspi = _pyatspi()
        locator = _identification(identification, name=name, role=role, accessible_id=accessible_id)
        match, trace = _select_accessible(pyatspi.Registry.getDesktop(0), locator)
        action = match.queryAction()
        available: list[str] = []
        for index in range(action.nActions):
            action_name = str(action.getName(index))
            available.append(action_name)
            if action_name.casefold() in {"click", "press", "activate"}:
                if not action.doAction(index):
                    raise RuntimeError(f"AT-SPI action {action_name!r} reported failure")
                return {
                    "action": action_name,
                    "resolution_stages": [stage.to_dict() for stage in trace],
                }
        if available:
            raise RuntimeError(
                "AT-SPI component exposes no supported activation action; "
                f"available actions: {', '.join(available)}"
            )
        raise RuntimeError("AT-SPI component exposes no actions")

    def get_text(self, *, identification: Mapping[str, Any] | AtspiIdentification) -> str:
        match, _ = _select_accessible(_pyatspi().Registry.getDesktop(0), _identification(identification))
        try:
            text = match.queryText()
            return str(text.getText(0, text.characterCount))
        except Exception as exc:
            raise RuntimeError("AT-SPI component does not expose readable text") from exc

    def set_text(self, value: str, *, identification: Mapping[str, Any] | AtspiIdentification) -> dict[str, Any]:
        match, trace = _select_accessible(_pyatspi().Registry.getDesktop(0), _identification(identification))
        try:
            editable = match.queryEditableText()
            # pyatspi implementations commonly return ``None`` for a
            # successful D-Bus void method; only an explicit False is failure.
            if editable.setTextContents(value) is False:
                raise RuntimeError("AT-SPI editable text interface rejected the value")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("AT-SPI component does not expose editable text") from exc
        return {"text": value, "resolution_stages": [stage.to_dict() for stage in trace]}

    def get_selection(self, *, identification: Mapping[str, Any] | AtspiIdentification) -> list[str]:
        match, _ = _select_accessible(_pyatspi().Registry.getDesktop(0), _identification(identification))
        try:
            selection = match.querySelection()
            return [str(selection.getSelectedChild(index).name) for index in range(selection.nSelectedChildren)]
        except Exception as exc:
            raise RuntimeError("AT-SPI component does not expose selection") from exc

    def select_child(self, child_index: int, *, identification: Mapping[str, Any] | AtspiIdentification) -> dict[str, Any]:
        if child_index < 0:
            raise ValueError("AT-SPI selection child index must be non-negative")
        match, trace = _select_accessible(_pyatspi().Registry.getDesktop(0), _identification(identification))
        try:
            selection = match.querySelection()
            if not selection.selectChild(child_index):
                raise RuntimeError(f"AT-SPI selection rejected child index {child_index}")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("AT-SPI component does not expose selectable children") from exc
        return {"child_index": child_index, "resolution_stages": [stage.to_dict() for stage in trace]}

    def get_value(self, *, identification: Mapping[str, Any] | AtspiIdentification) -> float:
        match, _ = _select_accessible(_pyatspi().Registry.getDesktop(0), _identification(identification))
        try:
            return float(match.queryValue().currentValue)
        except Exception as exc:
            raise RuntimeError("AT-SPI component does not expose a numeric value") from exc

    def set_value(self, value: float, *, identification: Mapping[str, Any] | AtspiIdentification) -> dict[str, Any]:
        match, trace = _select_accessible(_pyatspi().Registry.getDesktop(0), _identification(identification))
        try:
            value_interface = match.queryValue()
            setter = getattr(value_interface, "set_currentValue", None)
            if callable(setter):
                result = setter(float(value))
            else:
                value_interface.currentValue = float(value)
                result = None
            if result is False:
                raise RuntimeError(f"AT-SPI value interface rejected {value}")
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError("AT-SPI component does not expose a writable numeric value") from exc
        return {"value": float(value), "resolution_stages": [stage.to_dict() for stage in trace]}


def _pyatspi():
    try:
        import pyatspi  # type: ignore
    except ImportError as exc:
        raise AtspiUnavailable("pyatspi is not installed") from exc
    return pyatspi


def _identification(
    value: Mapping[str, Any] | AtspiIdentification | None,
    *,
    name: str | None = None,
    role: str | None = None,
    accessible_id: str | None = None,
) -> AtspiIdentification:
    if isinstance(value, AtspiIdentification):
        return value
    if value is None:
        mandatory = {
            key: item
            for key, item in (
                ("name", name),
                ("role", role),
                ("accessible_id", accessible_id),
            )
            if item is not None
        }
        if not mandatory:
            raise ValueError("AT-SPI identification requires at least one condition")
        return AtspiIdentification(mandatory=mandatory)

    raw = dict(value)
    if "mandatory" not in raw and any(key in raw for key in ("name", "role", "accessible_id", "application", "window", "parent")):
        # Compatibility for callers that supply a flat criteria mapping.
        return AtspiIdentification(mandatory=raw)

    mandatory = raw.get("mandatory", {})
    assistive = raw.get("assistive", {})
    ordinal = raw.get("ordinal")
    if not isinstance(mandatory, Mapping) or not mandatory:
        raise ValueError("AT-SPI identification.mandatory must be a non-empty mapping")
    if not isinstance(assistive, Mapping):
        raise ValueError("AT-SPI identification.assistive must be a mapping")
    if isinstance(ordinal, Mapping):
        ordinal = ordinal.get("index")
    if ordinal is not None and (not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0):
        raise ValueError("AT-SPI identification.ordinal must be a non-negative integer")
    return AtspiIdentification(mandatory=dict(mandatory), assistive=dict(assistive), ordinal=ordinal)


def _select_accessible(
    desktop: Any,
    identification: AtspiIdentification,
) -> tuple[Any, tuple[AtspiResolutionStage, ...]]:
    candidates, stages = _progressive_candidates(desktop, identification)
    if not candidates:
        last = stages[-1] if stages else None
        raise AtspiObjectNotFound(
            "AT-SPI component not found after applying locator conditions"
            + (f": {dict(last.criteria)!r}" if last else "")
        )
    if len(candidates) == 1:
        return candidates[0], stages
    if identification.ordinal is not None:
        if identification.ordinal >= len(candidates):
            raise AtspiObjectNotFound(
                f"AT-SPI ordinal {identification.ordinal} is outside {len(candidates)} matching candidates"
            )
        ordinal_stage = AtspiResolutionStage(
            source=f"ordinal:{identification.ordinal}",
            criteria=stages[-1].criteria if stages else dict(identification.mandatory),
            matches=len(candidates),
        )
        return candidates[identification.ordinal], (*stages, ordinal_stage)
    raise AtspiAmbiguousObject(
        f"AT-SPI locator remains ambiguous after all conditions: {len(candidates)} runtime objects match; "
        "add an assistive property, parent/window/application scope, or an explicit ordinal"
    )


def _progressive_candidates(
    desktop: Any,
    identification: AtspiIdentification,
) -> tuple[list[Any], tuple[AtspiResolutionStage, ...]]:
    criteria: dict[str, Any] = dict(identification.mandatory)
    candidates = _matching_accessibles(desktop, criteria)
    stages: list[AtspiResolutionStage] = [
        AtspiResolutionStage("mandatory", dict(criteria), len(candidates))
    ]
    if len(candidates) <= 1:
        return candidates, tuple(stages)

    for key, value in identification.assistive.items():
        criteria[key] = value
        candidates = [candidate for candidate in candidates if _matches_criteria(candidate, {key: value})]
        stages.append(AtspiResolutionStage(f"assistive:{key}", dict(criteria), len(candidates)))
        if len(candidates) <= 1:
            break
    return candidates, tuple(stages)


def _matching_accessibles(node: Any, criteria: Mapping[str, Any]) -> list[Any]:
    if not criteria:
        raise ValueError("AT-SPI matching requires at least one condition")
    matches: list[Any] = []
    _collect_accessibles(node, matches, criteria=criteria)
    return matches


def _collect_accessibles(node: Any, matches: list[Any], *, criteria: Mapping[str, Any]) -> None:
    if _matches_criteria(node, criteria):
        matches.append(node)
    for child in _children(node):
        _collect_accessibles(child, matches, criteria=criteria)


def _matches_criteria(node: Any, criteria: Mapping[str, Any]) -> bool:
    try:
        return all(_matches_condition(node, key, value) for key, value in criteria.items())
    except Exception:
        # AT-SPI events may hold an object path that becomes defunct as a
        # control redraws. It is not a candidate in a fresh live-tree query.
        return False


def _matches_condition(node: Any, key: str, expected: Any) -> bool:
    if key == "name":
        return getattr(node, "name", None) == expected
    if key == "role":
        actual = _role_name(node)
        return actual is not None and str(actual).casefold() == str(expected).casefold()
    if key == "accessible_id":
        return _accessible_id(node) == expected
    if key == "application":
        return _application_name(node) == expected
    if key == "window":
        return _window_name(node) == expected
    if key == "parent":
        if not isinstance(expected, Mapping):
            return False
        parent = _parent(node)
        return parent is not None and _matches_criteria(parent, expected)
    if key == "hierarchy":
        if not isinstance(expected, (list, tuple)):
            return False
        actual = _hierarchy(node)
        wanted = tuple(str(item) for item in expected)
        return len(wanted) <= len(actual) and actual[-len(wanted):] == wanted if wanted else False
    if key.startswith("attribute:"):
        return _attributes(node).get(key.split(":", 1)[1]) == expected
    raise ValueError(f"unsupported AT-SPI locator property {key!r}")


def _deepest_at_point(node: Any, *, x: int, y: int, pyatspi: Any) -> Any | None:
    # Desktop and application nodes commonly expose no Component interface
    # (and therefore no bounds). They are structural containers, not proof
    # that their descendants cannot contain the point. Traverse them while
    # retaining reverse child order as the best available stacking order.
    matches: list[Any] = []
    for child in reversed(_children(node)):
        bounds = _bounds(child, pyatspi)
        if bounds is None:
            deeper = _deepest_at_point(child, x=x, y=y, pyatspi=pyatspi)
            if deeper is not None:
                matches.append(deeper)
            continue
        left, top, width, height = bounds
        if not (left <= x < left + width and top <= y < top + height):
            continue
        deeper = _deepest_at_point(child, x=x, y=y, pyatspi=pyatspi)
        if deeper is not None:
            matches.append(deeper)
        else:
            matches.append(child)
    if matches:
        # AT-SPI does not provide a portable z-order. The smallest live
        # containing object is the most specific target and avoids selecting
        # GNOME's desktop-sized background panel over an application control.
        def area(candidate: Any) -> int:
            candidate_bounds = _bounds(candidate, pyatspi)
            if candidate_bounds is None:
                return 2**63 - 1
            return max(candidate_bounds[2], 0) * max(candidate_bounds[3], 0)

        return min(matches, key=area)
    bounds = _bounds(node, pyatspi)
    if bounds is not None:
        left, top, width, height = bounds
        if left <= x < left + width and top <= y < top + height:
            return node
    return None


def _capture_accessible(node: Any, pyatspi: Any) -> CapturedComponent:
    attributes = _attributes(node)
    bounds = _bounds(node, pyatspi)
    actions = _actions(node)
    hierarchy = _hierarchy(node)
    description = getattr(node, "description", None)
    state = _component_state(node, pyatspi, attributes=attributes, bounds=bounds)
    parent = _parent(node)
    return CapturedComponent(
        name=getattr(node, "name", None),
        role=_role_name(node),
        description=str(description) if description else None,
        accessible_id=_accessible_id(node, attributes=attributes),
        application=_application_name(node),
        window=_window_name(node),
        hierarchy=hierarchy,
        actions=actions,
        bounds=bounds,
        state=state,
        backend_properties=attributes,
        parent_name=getattr(parent, "name", None) if parent is not None else None,
        parent_role=_role_name(parent) if parent is not None else None,
        parent_accessible_id=_accessible_id(parent) if parent is not None else None,
    )


def _component_state(
    node: Any,
    pyatspi: Any,
    *,
    attributes: dict[str, str] | None = None,
    bounds: tuple[int, int, int, int] | None = None,
) -> ComponentState:
    try:
        state_set = node.getState()
    except Exception:
        state_set = None

    def state(name: str) -> bool | None:
        constant = getattr(pyatspi, f"STATE_{name.upper()}", None)
        if state_set is None or constant is None:
            return None
        try:
            return bool(state_set.contains(constant))
        except Exception:
            return None

    properties: dict[str, Any] = dict(attributes or _attributes(node))
    properties.update(
        {
            "name": getattr(node, "name", None),
            "role": _role_name(node),
            "description": getattr(node, "description", None),
            "accessible_id": _accessible_id(node, attributes=attributes),
            "application": _application_name(node),
            "window": _window_name(node),
            "bounds": list(bounds) if bounds else None,
            "actions": list(_actions(node)),
        }
    )
    return ComponentState(
        present=True,
        visible=state("visible"),
        showing=state("showing"),
        enabled=state("enabled"),
        focused=state("focused"),
        selected=state("selected"),
        checked=state("checked"),
        pressed=state("pressed"),
        expanded=state("expanded"),
        editable=state("editable"),
        readonly=state("read_only"),
        active=state("active"),
        sensitive=state("sensitive"),
        properties=properties,
    )


def _focused_accessible(node: Any, pyatspi: Any) -> Any | None:
    """Return the first currently focused object in the desktop tree."""
    try:
        state = node.getState()
        focused = getattr(pyatspi, "STATE_FOCUSED", None)
        if focused is not None and state.contains(focused):
            return node
    except Exception:
        pass
    for child in _children(node):
        result = _focused_accessible(child, pyatspi)
        if result is not None:
            return result
    return None


def _accessible_signature(node: Any, pyatspi: Any) -> tuple[Any, ...] | None:
    if node is None:
        return None
    try:
        return (
            getattr(node, "name", None),
            _role_name(node),
            _application_name(node),
            _bounds(node, pyatspi),
        )
    except Exception:
        return None


def _children(node: Any) -> list[Any]:
    try:
        count = int(node.childCount)
    except Exception:
        return []
    result: list[Any] = []
    for index in range(count):
        try:
            child = node.getChildAtIndex(index)
        except Exception:
            continue
        if child is not None:
            result.append(child)
    return result


def _role_name(node: Any) -> str | None:
    try:
        return str(node.getRoleName())
    except Exception:
        return None


def _attributes(node: Any) -> dict[str, str]:
    try:
        raw = node.getAttributes()
    except Exception:
        return {}
    result: dict[str, str] = {}
    for item in raw or []:
        if not isinstance(item, str):
            continue
        key, separator, value = item.partition(":")
        if separator:
            result[key] = value
    return result


def _accessible_id(node: Any, *, attributes: dict[str, str] | None = None) -> str | None:
    for attribute_name in ("accessibleId", "id"):
        value = getattr(node, attribute_name, None)
        if value:
            return str(value)
    attrs = attributes if attributes is not None else _attributes(node)
    for key in ("id", "accessible-id", "accessible_id", "automation-id"):
        value = attrs.get(key)
        if value:
            return value
    return None


def _actions(node: Any) -> tuple[str, ...]:
    try:
        action = node.queryAction()
    except Exception:
        return ()
    result: list[str] = []
    for index in range(action.nActions):
        try:
            result.append(str(action.getName(index)))
        except Exception:
            continue
    return tuple(result)


def _bounds(node: Any, pyatspi: Any) -> tuple[int, int, int, int] | None:
    try:
        component = node.queryComponent()
        extents = component.getExtents(pyatspi.DESKTOP_COORDS)
        bounds = (int(extents.x), int(extents.y), int(extents.width), int(extents.height))
        # Java ATK uses (-1, -1, -1, -1) for structural containers such as
        # frames and panels. Treat it as absent geometry so point traversal
        # continues into their bounded descendants.
        if bounds[2] <= 0 or bounds[3] <= 0:
            return None
        return bounds
    except Exception:
        return None


def _parent(node: Any) -> Any | None:
    try:
        return node.parent
    except Exception:
        return None


def _ancestors(node: Any) -> list[Any]:
    values: list[Any] = []
    current = node
    seen: set[int] = set()
    for _ in range(64):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        values.append(current)
        current = _parent(current)
    return values


def _application_name(node: Any) -> str | None:
    for current in _ancestors(node):
        role = (_role_name(current) or "").casefold()
        if role == "application":
            name = getattr(current, "name", None)
            return str(name) if name else None
    return None


def _live_application(desktop: Any, application_name: str) -> Any | None:
    """Find a fresh top-level application proxy by its live AT-SPI name."""
    fallback = None
    # New registrations are appended. Prefer the newest matching proxy with
    # a live child tree; Java ATK can retain empty proxies after app restarts.
    for candidate in reversed(_children(desktop)):
        if (_role_name(candidate) or "").casefold() != "application":
            continue
        try:
            if candidate.name == application_name:
                fallback = candidate
                if _children(candidate):
                    return candidate
        except Exception:
            continue
    return fallback


def _window_name(node: Any) -> str | None:
    window_roles = {"frame", "window", "dialog", "alert"}
    for current in _ancestors(node):
        role = (_role_name(current) or "").casefold()
        if role in window_roles:
            name = getattr(current, "name", None)
            return str(name) if name else None
    return None


def _hierarchy(node: Any) -> tuple[str, ...]:
    values: list[str] = []
    for current in reversed(_ancestors(node)):
        label = getattr(current, "name", None) or _role_name(current)
        if label:
            values.append(str(label))
    return tuple(values)


if TYPE_CHECKING:
    from automation_harness.core.test_context import TestContext
