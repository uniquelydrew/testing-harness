from __future__ import annotations

import json
import os
import queue
import socket
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from automation_harness.models.component import CapturedComponent, ComponentState, ComponentStrategy, ResolvedComponent


class JavaFxBridgeUnavailable(RuntimeError):
    pass


class JavaFxBridgeProtocolError(RuntimeError):
    pass


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
                    return _captured(endpoint, node)
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
                    return _captured(endpoint, node)
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
    mandatory = {}
    assistive = {}
    node_id = _optional_str(node.get("id"))
    role = _optional_str(node.get("accessible_role"))
    accessible_text = _optional_str(node.get("accessible_text"))
    text = _optional_str(node.get("text"))
    native_class = _optional_str(node.get("class"))
    window = _optional_str(node.get("window"))
    parent = node.get("parent") if isinstance(node.get("parent"), Mapping) else {}

    if node_id:
        mandatory["id"] = node_id
    elif accessible_text:
        mandatory["accessible_text"] = accessible_text
        if role:
            mandatory["accessible_role"] = role
    elif text:
        mandatory["text"] = text
        if native_class:
            mandatory["class"] = native_class
    elif native_class:
        mandatory["class"] = native_class

    if native_class and "class" not in mandatory:
        assistive["class"] = native_class
    if role and "accessible_role" not in mandatory:
        assistive["accessible_role"] = role
    if window:
        assistive["window"] = window

    parent_identity = {}
    parent_id = _optional_str(parent.get("id"))
    parent_text = _optional_str(parent.get("accessible_text") or parent.get("text"))
    parent_class = _optional_str(parent.get("class"))
    if parent_id:
        parent_identity["id"] = parent_id
    elif parent_text:
        parent_identity["accessible_text"] = parent_text
    elif parent_class:
        parent_identity["class"] = parent_class
    if parent_identity:
        assistive["parent"] = parent_identity

    if not mandatory:
        raise ValueError("captured JavaFX node exposes no durable identification properties")
    result = {"mandatory": mandatory}
    if assistive:
        result["assistive"] = assistive
    return result


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


def _monotonic():
    import time
    return time.monotonic()
