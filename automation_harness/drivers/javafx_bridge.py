from __future__ import annotations

import json
import os
import queue
import socket
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from automation_harness.core.semantic_target import SemanticTargetResolution
from automation_harness.models.component import CapturedComponent, ComponentState, ComponentStrategy, ResolvedComponent
from automation_harness.models.gui import ObjectType, classify_accessibility


class JavaFxBridgeUnavailable(RuntimeError):
    pass


class JavaFxBridgeProtocolError(RuntimeError):
    pass


class JavaFxRecordingTransport(Protocol):
    def request(self, operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True)
class HttpJavaFxBridgeTransport:
    """Loopback-only HTTP transport used by the recording agent endpoints."""
    endpoint: str
    token: str
    timeout: float = 5.0

    def request(self, operation: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.endpoint.startswith(("http://127.0.0.1", "http://localhost")):
            raise ValueError("JavaFX agent endpoint must be loopback-only")
        request = Request(
            f"{self.endpoint.rstrip('/')}/{operation}", data=json.dumps(dict(payload)).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Automation-Harness-Token": self.token}, method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"JavaFX agent {operation} failed: HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"JavaFX agent {operation} is unavailable: {exc.reason}") from exc
        if not isinstance(value, Mapping):
            raise RuntimeError(f"JavaFX agent {operation} returned a non-object response")
        if value.get("ok") is False:
            raise RuntimeError(f"JavaFX agent {operation} failed: {value.get('error', 'unknown error')}")
        result = value.get("result", value)
        if not isinstance(result, Mapping):
            raise RuntimeError(f"JavaFX agent {operation} result must be an object")
        return result


@dataclass(frozen=True)
class JavaFxBridgeEndpoint:
    pid: int
    host: str
    port: int
    token: str
    discovery_file: Path
    command: str | None = None
    java_version: str | None = None

    def request(self, op: str, *, timeout: float = 5.0, **payload: Any) -> dict[str, Any]:
        request = {"token": self.token, "op": op}
        request.update(payload)
        try:
            sock = socket.create_connection((self.host, self.port), timeout=timeout)
        except OSError as exc:
            raise JavaFxBridgeUnavailable(
                "JavaFX bridge endpoint %s:%s for pid %s is unavailable: %s"
                % (self.host, self.port, self.pid, exc)
            ) from exc
        try:
            sock.settimeout(timeout)
            writer = sock.makefile("wb")
            reader = sock.makefile("rb")
            writer.write((json.dumps(request, separators=(",", ":")) + "\n").encode("utf-8"))
            writer.flush()
            raw = reader.readline()
            if not raw:
                raise JavaFxBridgeProtocolError("JavaFX bridge closed the connection without a response")
            response = json.loads(raw.decode("utf-8"))
            if not isinstance(response, dict):
                raise JavaFxBridgeProtocolError("JavaFX bridge response is not an object")
            if not response.get("ok", False):
                raise JavaFxBridgeProtocolError(str(response.get("error", "JavaFX bridge request failed")))
            return response
        except socket.timeout as exc:
            raise TimeoutError("JavaFX bridge request %r timed out after %gs" % (op, timeout)) from exc
        finally:
            try:
                sock.close()
            except OSError:
                pass


@dataclass(frozen=True)
class JavaFxResolutionStage:
    source: str
    criteria: Mapping[str, Any]
    matches: int

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "criteria": dict(self.criteria), "matches": self.matches}


@dataclass
class JavaFxBridgeDriver:
    """Native JavaFX scene-graph driver backed by the in-process bridge agent.

    Linux OpenJFX does not publish its scene graph through AT-SPI. This driver
    therefore uses the Automation Harness Java agent to read and interact with
    public JavaFX Nodes directly, while leaving Swing/AT-SPI support unchanged.
    """

    context: Any = None
    discovery_dir: Path | None = None

    @property
    def available(self) -> bool:
        return bool(self.endpoints())

    def endpoints(self) -> tuple[JavaFxBridgeEndpoint, ...]:
        return discover_javafx_endpoints(self.discovery_dir)

    @classmethod
    def application_present(cls, expected_application: str | None = None) -> bool:
        driver = cls()
        for endpoint in driver.endpoints():
            try:
                response = endpoint.request("windows", timeout=1.0)
            except Exception:
                continue
            for window in response.get("windows", []):
                title = str(window.get("title") or "")
                if expected_application is None or expected_application.casefold() in title.casefold():
                    return True
        return False

    def capture_next_click(self, *, timeout: float = 30.0) -> CapturedComponent:
        endpoints = self.endpoints()
        if not endpoints:
            raise JavaFxBridgeUnavailable("no active JavaFX bridge endpoints were discovered")

        results: "queue.Queue[tuple[str, Any, Any]]" = queue.Queue()

        def worker(endpoint: JavaFxBridgeEndpoint) -> None:
            try:
                response = endpoint.request(
                    "capture_next_click",
                    timeout=max(1.0, timeout + 2.0),
                    timeout_ms=max(1, int(timeout * 1000)),
                )
                results.put(("ok", endpoint, response))
            except Exception as exc:
                results.put(("error", endpoint, exc))

        for endpoint in endpoints:
            thread = threading.Thread(
                target=worker,
                args=(endpoint,),
                name="javafx-capture-%s" % endpoint.pid,
                daemon=True,
            )
            thread.start()

        errors = []
        deadline = _monotonic() + timeout + 2.5
        remaining = len(endpoints)
        while remaining:
            wait = max(0.01, deadline - _monotonic())
            if wait <= 0:
                break
            try:
                status, endpoint, value = results.get(timeout=wait)
            except queue.Empty:
                break
            remaining -= 1
            if status == "ok":
                node = value.get("node")
                if isinstance(node, Mapping):
                    return self._captured_for_capture(endpoint, node)
                errors.append("pid %s returned no node" % endpoint.pid)
            else:
                errors.append("pid %s: %s: %s" % (endpoint.pid, type(value).__name__, value))
        raise TimeoutError(
            "no JavaFX bridge captured a click within %gs%s"
            % (timeout, ("; " + "; ".join(errors)) if errors else "")
        )

    def capture_at_point(self, x: int, y: int) -> CapturedComponent:
        errors = []
        for endpoint in self.endpoints():
            try:
                response = endpoint.request("hit_test", timeout=2.0, x=x, y=y)
                node = response.get("node")
                if isinstance(node, Mapping):
                    return self._captured_for_capture(endpoint, node)
            except Exception as exc:
                errors.append("pid %s: %s" % (endpoint.pid, exc))
        raise LookupError(
            "no JavaFX node found at (%s, %s)%s"
            % (x, y, ("; " + "; ".join(errors)) if errors else "")
        )

    def inspect(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> CapturedComponent:
        endpoint, node, _trace = self._find_unique(identification)
        return _captured(endpoint, node)

    def resolve(
        self,
        component_id: str,
        *,
        identification: Mapping[str, Any] | None = None,
        **_kwargs: Any,
    ) -> ResolvedComponent:
        endpoint, node, trace = self._find_unique(identification)
        captured = _captured(endpoint, node)
        metadata = captured.to_dict()
        metadata["resolution"] = {
            "identification": dict(identification or {}),
            "stages": [stage.to_dict() for stage in trace],
            "bridge_pid": endpoint.pid,
            "bridge_port": endpoint.port,
        }
        return ResolvedComponent(component_id=component_id, strategy="javafx", metadata=metadata)

    def state(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> ComponentState:
        return self.inspect(identification=identification).state

    def activate_window(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        endpoint, _node, _trace = self._find_unique(identification)
        response = endpoint.request("activate_window", timeout=5.0, identification=dict(identification or {}))
        return {
            "operation": "activate_window",
            "bridge_pid": endpoint.pid,
            "window": response.get("window"),
            "focused": response.get("focused"),
        }

    def focus(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        endpoint, _node, _trace = self._find_unique(identification)
        response = endpoint.request("focus", timeout=5.0, identification=dict(identification or {}))
        if response.get("focused") is not True:
            raise RuntimeError("JavaFX node did not report focused state")
        return {
            "operation": "focus",
            "bridge_pid": endpoint.pid,
            "node": response.get("node"),
            "focused": True,
        }

    def activate(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
        endpoint, _node, _trace = self._find_unique(identification)
        response = endpoint.request("activate", timeout=5.0, identification=dict(identification or {}))
        return {"action": "activate", "bridge_pid": endpoint.pid, "node": response.get("node")}

    def get_text(self, *, identification: Mapping[str, Any] | None = None, **_kwargs: Any) -> str:
        endpoint, _node, _trace = self._find_unique(identification)
        response = endpoint.request("get_text", timeout=5.0, identification=dict(identification or {}))
        return str(response.get("text") or "")

    def set_text(
        self,
        value: str,
        *,
        identification: Mapping[str, Any] | None = None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        endpoint, _node, _trace = self._find_unique(identification)
        response = endpoint.request(
            "set_text",
            timeout=5.0,
            identification=dict(identification or {}),
            value=value,
        )
        return {"action": "set_text", "bridge_pid": endpoint.pid, "node": response.get("node")}

    def count_matches(self, *, identification: Mapping[str, Any] | None = None) -> int:
        matches, _trace = self._find_matches(identification)
        return len(matches)

    def assess_identification(self, identification: Mapping[str, Any]) -> tuple[JavaFxResolutionStage, ...]:
        _matches, trace = self._find_matches(identification)
        return trace

    def _captured_for_capture(
        self,
        endpoint: JavaFxBridgeEndpoint,
        node: Mapping[str, Any],
    ) -> CapturedComponent:
        """Build a durable capture and infer an ordinal only as a last resort.

        JavaFX skins commonly create repeated internal Nodes with no id or
        text. The candidate identity first uses semantic application metadata,
        stable ancestor lineage, and layout constraints. If that complete
        stable identity still matches siblings, the bridge determines which
        candidate generated the click from its transient Node reference and
        persists that scoped position as an explicit ordinal.
        """
        captured = _captured(endpoint, node)
        strategy = captured.candidate_strategy()
        identity = strategy.options.get("identification")
        if not isinstance(identity, Mapping):
            return captured
        identity = dict(identity)
        try:
            response = endpoint.request("find", timeout=2.0, identification=identity)
            matches = [item for item in response.get("matches", []) if isinstance(item, Mapping)]
        except Exception:
            return captured
        if len(matches) <= 1:
            return captured
        target_ref = node.get("ref")
        if target_ref is None:
            return captured
        ordinal = next(
            (index for index, candidate in enumerate(matches) if candidate.get("ref") == target_ref),
            None,
        )
        if ordinal is None:
            return captured
        identity["ordinal"] = ordinal
        return replace(
            captured,
            authored_strategy=ComponentStrategy("javafx", {"identification": identity}),
        )

    def _find_unique(
        self,
        identification: Mapping[str, Any] | None,
    ) -> tuple[JavaFxBridgeEndpoint, Mapping[str, Any], tuple[JavaFxResolutionStage, ...]]:
        matches, trace = self._find_matches(identification)
        raw = dict(identification or {})
        ordinal = raw.get("ordinal")
        if len(matches) > 1 and isinstance(ordinal, int) and not isinstance(ordinal, bool):
            if ordinal < 0 or ordinal >= len(matches):
                raise LookupError("JavaFX ordinal %s is outside %s matches" % (ordinal, len(matches)))
            matches = [matches[ordinal]]
        if not matches:
            raise LookupError("JavaFX component not found: %r" % (identification,))
        if len(matches) > 1:
            raise LookupError("JavaFX component is ambiguous: %r (%s matches)" % (identification, len(matches)))
        endpoint, node = matches[0]
        return endpoint, node, trace

    def _find_matches(
        self,
        identification: Mapping[str, Any] | None,
    ) -> tuple[list[tuple[JavaFxBridgeEndpoint, Mapping[str, Any]]], tuple[JavaFxResolutionStage, ...]]:
        endpoints = self.endpoints()
        if not endpoints:
            raise JavaFxBridgeUnavailable("no active JavaFX bridge endpoints were discovered")
        identity = dict(identification or {})
        matches = []
        stage_totals = []
        errors = []
        for endpoint in endpoints:
            try:
                response = endpoint.request("find", timeout=3.0, identification=identity)
            except Exception as exc:
                errors.append("pid %s: %s" % (endpoint.pid, exc))
                continue
            for node in response.get("matches", []):
                if isinstance(node, Mapping):
                    matches.append((endpoint, node))
            stage_totals.append(response.get("stages", []))
        if errors and not stage_totals:
            raise JavaFxBridgeUnavailable("all JavaFX bridge endpoints failed: " + "; ".join(errors))
        trace = _merge_stages(stage_totals)
        return matches, trace


@dataclass
class JavaFxRecordingBridge:
    """Semantic-capture view over the recording agent's HTTP protocol."""
    transport: JavaFxRecordingTransport

    def capture_next_click(self, *, timeout: float = 30.0) -> CapturedComponent:
        return self.semantic_target(self.transport.request("capture_next_click", {"timeout": timeout})).capture()

    def hit_test(self, x: int, y: int) -> CapturedComponent:
        return self.semantic_target(self.transport.request("hit_test", {"x": x, "y": y})).capture()

    @staticmethod
    def semantic_target(response: Mapping[str, Any]) -> SemanticTargetResolution:
        physical = _captured_recording_node(_require_mapping(response, "physical_node", fallback="node"))
        semantic = _captured_recording_node(_require_mapping(response, "semantic_node", fallback="node"))
        promotion = response.get("promotion", {})
        if not isinstance(promotion, Mapping):
            raise ValueError("JavaFX capture response promotion must be an object")
        return SemanticTargetResolution(
            physical, semantic, bool(promotion.get("promoted", physical != semantic)),
            {key: value for key, value in promotion.items() if key != "promoted"},
        )


def discover_javafx_endpoints(discovery_dir: Path | None = None) -> tuple[JavaFxBridgeEndpoint, ...]:
    directory = discovery_dir or _default_discovery_dir()
    if not directory.is_dir():
        return ()
    values = []
    for path in sorted(directory.glob("javafx-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            endpoint = JavaFxBridgeEndpoint(
                pid=int(payload["pid"]),
                host=str(payload.get("host") or "127.0.0.1"),
                port=int(payload["port"]),
                token=str(payload["token"]),
                discovery_file=path,
                command=_optional_str(payload.get("command")),
                java_version=_optional_str(payload.get("java_version")),
            )
            response = endpoint.request("ping", timeout=0.5)
            if response.get("javafx_available", False):
                values.append(endpoint)
        except Exception:
            continue
    return tuple(values)


def _default_discovery_dir() -> Path:
    configured = os.environ.get("AUTOMATION_HARNESS_JAVAFX_DISCOVERY_DIR")
    return Path(configured).expanduser() if configured else Path("/tmp/automation-harness-javafx")


def _captured(endpoint: JavaFxBridgeEndpoint, node: Mapping[str, Any]) -> CapturedComponent:
    node_id = _optional_str(node.get("id"))
    role = _role(node.get("accessible_role"))
    text = _optional_str(node.get("text"))
    accessible_text = _optional_str(node.get("accessible_text"))
    native_class = _optional_str(node.get("class"))
    window = _optional_str(node.get("window"))
    name = accessible_text or text or node_id or _optional_str(node.get("simple_class"))
    bounds = _bounds(node.get("bounds"))
    parent = node.get("parent") if isinstance(node.get("parent"), Mapping) else {}
    actions = tuple(str(value) for value in node.get("actions", []) if value is not None)
    identity = _candidate_identification(node)
    strategy = ComponentStrategy("javafx", {"identification": identity})
    state = ComponentState(
        present=True,
        visible=_optional_bool(node.get("visible")),
        showing=_optional_bool(node.get("visible")),
        enabled=None if node.get("disabled") is None else not bool(node.get("disabled")),
        focused=_optional_bool(node.get("focused")),
        properties={
            "managed": node.get("managed"),
            "focus_traversable": node.get("focus_traversable"),
        },
    )
    properties = {
        "bridge_pid": endpoint.pid,
        "bridge_port": endpoint.port,
        "node_ref": node.get("ref"),
        "javafx_id": node_id,
        "style_classes": list(node.get("style_classes") or []),
        "accessible_role": node.get("accessible_role"),
        "accessible_text": accessible_text,
        "text": text,
        "hierarchy": list(node.get("hierarchy") or []),
        "stable_ancestors": list(node.get("stable_ancestors") or []),
        "user_data": node.get("user_data"),
        "node_properties": dict(node.get("properties") or {}) if isinstance(node.get("properties"), Mapping) else {},
        "layout": dict(node.get("layout") or {}) if isinstance(node.get("layout"), Mapping) else {},
        "sibling_index": node.get("sibling_index"),
        "sibling_count": node.get("sibling_count"),
    }
    return CapturedComponent(
        name=name,
        role=role,
        description=_optional_str(node.get("accessible_help")),
        accessible_id=node_id,
        application=window,
        window=window,
        hierarchy=tuple(str(value) for value in node.get("hierarchy", []) if value is not None),
        actions=actions,
        bounds=bounds,
        state=state,
        backend_properties=properties,
        parent_name=_optional_str(parent.get("accessible_text") or parent.get("text") or parent.get("id")),
        parent_role=_role(parent.get("accessible_role")),
        parent_accessible_id=_optional_str(parent.get("id")),
        authored_strategy=strategy,
        framework="javafx",
        native_class=native_class,
    )


def _candidate_identification(node: Mapping[str, Any]) -> dict[str, Any]:
    """Compose identity from semantic evidence before structural fallbacks.

    The ordering is intentional: explicit IDs and application-authored metadata
    outrank JavaFX implementation classes; stable lineage/layout outrank literal
    hierarchy; ordinal selection is added later only when this identity still
    resolves multiple runtime Nodes.
    """
    mandatory = {}
    assistive = {}
    node_id = _optional_str(node.get("id"))
    role = _optional_str(node.get("accessible_role"))
    accessible_text = _optional_str(node.get("accessible_text"))
    text = _optional_str(node.get("text"))
    native_class = _optional_str(node.get("class"))
    window = _optional_str(node.get("window"))
    hierarchy = _string_list(node.get("hierarchy"))
    lineage = _mapping_list(node.get("stable_ancestors"))
    parent = node.get("parent") if isinstance(node.get("parent"), Mapping) else {}
    layout = dict(node.get("layout") or {}) if isinstance(node.get("layout"), Mapping) else {}
    user_data = node.get("user_data") if _is_scalar(node.get("user_data")) else None
    properties = dict(node.get("properties") or {}) if isinstance(node.get("properties"), Mapping) else {}
    domain_properties = _domain_identity_properties(properties)
    style_classes = _string_list(node.get("style_classes"))
    internal_class = _is_internal_javafx_class(native_class)

    if node_id:
        mandatory["id"] = node_id
    elif domain_properties:
        mandatory["properties"] = domain_properties
    elif accessible_text:
        mandatory["accessible_text"] = accessible_text
        if role:
            mandatory["accessible_role"] = role
    elif text:
        mandatory["text"] = text
        if native_class and not internal_class:
            mandatory["class"] = native_class
        elif role:
            mandatory["accessible_role"] = role
    elif user_data not in (None, ""):
        mandatory["user_data"] = user_data
        if native_class and not internal_class:
            mandatory["class"] = native_class
    elif native_class and not internal_class:
        mandatory["class"] = native_class
    elif role:
        mandatory["accessible_role"] = role
    elif native_class:
        mandatory["class"] = native_class

    if domain_properties and "properties" not in mandatory:
        assistive["properties"] = domain_properties
    if user_data not in (None, "") and "user_data" not in mandatory:
        assistive["user_data"] = user_data
    if native_class and "class" not in mandatory:
        assistive["class"] = native_class
    if role and "accessible_role" not in mandatory:
        assistive["accessible_role"] = role
    if window:
        assistive["window"] = window

    parent_identity = _parent_identity(parent)
    if parent_identity:
        assistive["parent"] = parent_identity
    if layout:
        assistive["layout"] = layout
    if lineage:
        assistive["lineage"] = lineage
    elif hierarchy:
        assistive["hierarchy"] = hierarchy
    if style_classes and not lineage and not layout:
        assistive["style_classes"] = style_classes

    if not mandatory:
        raise ValueError("captured JavaFX node exposes no durable identification properties")
    result = {"mandatory": mandatory}
    if assistive:
        result["assistive"] = assistive
    return result


def _parent_identity(parent: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    parent_id = _optional_str(parent.get("id"))
    parent_text = _optional_str(parent.get("accessible_text") or parent.get("text"))
    parent_class = _optional_str(parent.get("class"))
    if parent_id:
        result["id"] = parent_id
    elif parent_text:
        result["accessible_text"] = parent_text
    elif parent_class and not _is_internal_javafx_class(parent_class):
        result["class"] = parent_class
    return result


def _domain_identity_properties(properties: Mapping[str, Any]) -> dict[str, Any]:
    """Return application-authored property keys intended to survive layout changes."""
    result = {}
    for raw_key, value in properties.items():
        key = str(raw_key)
        folded = key.casefold()
        if not _is_scalar(value):
            continue
        if folded.startswith(("automation.", "test.", "qa.")):
            result[key] = value
    return result


def _is_internal_javafx_class(value: str | None) -> bool:
    return bool(value and (value.startswith("com.sun.javafx.") or value.startswith("com.sun.glass.")))


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if item is not None]


def _mapping_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping) and item]


def _merge_stages(raw_traces: list[Any]) -> tuple[JavaFxResolutionStage, ...]:
    merged = []
    max_len = max((len(trace) for trace in raw_traces if isinstance(trace, list)), default=0)
    for index in range(max_len):
        source = None
        criteria = None
        total = 0
        for trace in raw_traces:
            if not isinstance(trace, list) or index >= len(trace) or not isinstance(trace[index], Mapping):
                continue
            stage = trace[index]
            source = source or str(stage.get("source") or "stage-%s" % index)
            criteria = criteria or dict(stage.get("criteria") or {})
            total += int(stage.get("matches") or 0)
        if source is not None:
            merged.append(JavaFxResolutionStage(source, criteria or {}, total))
    return tuple(merged)


def _bounds(value: Any):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        left, top, width, height = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    return (round(left), round(top), max(0, round(width)), max(0, round(height)))


def _role(value: Any):
    if value is None:
        return None
    return str(value).replace("_", " ").casefold()


def _optional_str(value: Any):
    return str(value) if value is not None and str(value) != "" else None


def _optional_bool(value: Any):
    return bool(value) if value is not None else None


def _require_mapping(response: Mapping[str, Any], key: str, *, fallback: str) -> Mapping[str, Any]:
    value = response.get(key, response.get(fallback))
    if not isinstance(value, Mapping):
        raise ValueError("JavaFX capture response requires %s" % key)
    return value


def _captured_recording_node(node: Mapping[str, Any]) -> CapturedComponent:
    state_value = node.get("state", {})
    state = state_value if isinstance(state_value, Mapping) else {}
    bounds_value = node.get("bounds")
    bounds = tuple(int(value) for value in bounds_value) if isinstance(bounds_value, (list, tuple)) and len(bounds_value) == 4 else None
    native_class = _optional_str(node.get("native_class") or node.get("class"))
    role = _optional_str(node.get("role"))
    object_type_value = node.get("object_type")
    try:
        object_type = ObjectType(str(object_type_value)) if object_type_value else (
            ObjectType.LABEL if native_class in {"javafx.scene.text.Text", "javafx.scene.control.Label"}
            else classify_accessibility(role, native_class)
        )
    except ValueError:
        object_type = classify_accessibility(role, native_class)
    properties = node.get("properties") if isinstance(node.get("properties"), Mapping) else {}
    return CapturedComponent(
        name=_optional_str(node.get("name") or node.get("text")), role=role,
        description=_optional_str(node.get("description")), accessible_id=_optional_str(node.get("accessible_id")),
        application=_optional_str(node.get("application")), window=_optional_str(node.get("window")),
        hierarchy=tuple(str(item) for item in node.get("hierarchy", ()) if item is not None),
        actions=tuple(str(item) for item in node.get("actions", ()) if item is not None), bounds=bounds,
        state=ComponentState(present=bool(state.get("present", True)), visible=state.get("visible"), showing=state.get("showing"), enabled=state.get("enabled"), focused=state.get("focused"), selected=state.get("selected"), checked=state.get("checked"), editable=state.get("editable"), properties=dict(state.get("properties", {})) if isinstance(state.get("properties", {}), Mapping) else {}),
        backend_properties={**dict(properties), **({"ref": node["ref"]} if node.get("ref") else {})},
        object_type=object_type, framework="javafx", native_class=native_class,
    )


def _monotonic():
    import time
    return time.monotonic()