"""Client for the opt-in mixed Swing/JavaFX in-process HTTP agent."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from automation_harness.drivers.javafx_bridge import (
    HttpJavaFxBridgeTransport,
    _captured_recording_node,
)
from automation_harness.models.component import CapturedComponent, ResolvedComponent


class JavaAgentUnavailable(RuntimeError):
    pass


class JavaAgentDriver:
    def __init__(self, _context=None) -> None:
        self._transports = ()
        self.refresh_transports()

    @property
    def transports(self):
        """Return a fresh view of live mixed-agent endpoints.

        Discovery files are process-scoped and can appear after the harness has
        already started. Refreshing here prevents a driver instance from
        permanently caching an empty process set when the SUT is launched later.
        """
        return self.refresh_transports()

    def refresh_transports(self):
        self._transports = configured_java_agent_transports()
        return self._transports

    @property
    def available(self) -> bool:
        return bool(self.refresh_transports())

    def capture_at_point(
        self, x: int, y: int, *, process_id: int | None = None,
    ) -> CapturedComponent:
        transports = self.refresh_transports()
        if process_id is not None:
            transports = tuple(
                item for item in transports
                if getattr(item, "pid", None) == process_id
            )
            if not transports:
                raise JavaAgentUnavailable(
                    "no Automation Harness Java agent endpoint was discovered for pid %s"
                    % process_id
                )
        return self._first(
            "hit_test", {"x": int(x), "y": int(y)}, transports=transports,
        )

    def capture_next_click(self, *, timeout: float = 30.0) -> CapturedComponent:
        return self._first(
            "capture_next_click", {"timeout": float(timeout)},
            transports=self.refresh_transports(),
        )

    def inspect(self, *, identification: Mapping[str, Any] | None = None, **_kwargs) -> CapturedComponent:
        return self._first(
            "resolve", _identity_payload(identification),
            transports=self.refresh_transports(),
        )

    def resolve(self, component_id: str, *, identification=None, **kwargs) -> ResolvedComponent:
        captured = self.inspect(identification=identification, **kwargs)
        metadata = captured.to_dict()
        metadata["bounds"] = list(captured.bounds) if captured.bounds else None
        return ResolvedComponent(component_id, "java_agent", metadata)

    def state(self, *, identification=None, **kwargs):
        return self.inspect(identification=identification, **kwargs).state

    def activate(self, *, identification=None, **_kwargs):
        captured = self._first(
            "activate", _identity_payload(identification),
            transports=self.refresh_transports(),
        )
        return {"action": "activate", "component": captured.to_dict()}

    def _first(self, operation: str, payload: Mapping[str, Any], *, transports=None) -> CapturedComponent:
        transports = self.refresh_transports() if transports is None else tuple(transports)
        if not transports:
            raise JavaAgentUnavailable("no configured Automation Harness Java agent endpoint")
        errors = []
        for transport in transports:
            try:
                response = transport.request(operation, dict(payload))
                node = response.get("semantic_node", response.get("node"))
                if not isinstance(node, Mapping):
                    raise ValueError("Java agent response contains no semantic node")
                return _captured_recording_node(node)
            except Exception as exc:
                errors.append("%s: %s" % (type(exc).__name__, exc))
        raise JavaAgentUnavailable("all configured Java agents failed: " + "; ".join(errors))


def configured_java_agent_transports():
    """Return only mixed-agent endpoints, never legacy JavaFX endpoints.

    The two agents expose different runtime capabilities. Treating a legacy
    JavaFX endpoint as a mixed Swing/JOGL endpoint makes the router believe a
    JVM is fully instrumented when it is not.
    """
    urls = os.environ.get(
        "AUTOMATION_HARNESS_JAVA_AGENT_URLS",
        os.environ.get("AUTOMATION_HARNESS_JAVA_AGENT_URL", ""),
    ).split(",")
    tokens = os.environ.get(
        "AUTOMATION_HARNESS_JAVA_AGENT_TOKENS",
        os.environ.get("AUTOMATION_HARNESS_JAVA_AGENT_TOKEN", ""),
    ).split(",")
    configured = list(
        HttpJavaFxBridgeTransport(url.strip(), token.strip())
        for url, token in zip(urls, tokens)
        if url.strip() and token.strip()
    )
    configured.extend(discover_java_agent_transports())
    return tuple({(item.endpoint, item.token): item for item in configured}.values())


def discover_java_agent_transports(discovery_dir=None):
    directory = Path(discovery_dir) if discovery_dir is not None else Path(
        os.environ.get(
            "AUTOMATION_HARNESS_JAVA_AGENT_DISCOVERY_DIR",
            "/tmp/automation-harness-java-agent",
        )
    )
    if not directory.is_dir():
        return ()
    transports = []
    for path in sorted(directory.glob("java-*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("protocol") != "automation-harness-java-agent/1":
                continue
            host = str(raw.get("host") or "127.0.0.1")
            port = int(raw["port"])
            token = str(raw["token"])
            transport = HttpJavaFxBridgeTransport(
                "http://%s:%s" % (host, port), token, pid=int(raw["pid"]),
            )
            health = transport.request("health", {})
            if health.get("status") == "ok":
                transports.append(transport)
        except Exception:
            continue
    return tuple(transports)


def configured_java_recording_transports():
    """Return de-duplicated mixed-agent and legacy JavaFX recorders."""
    transports = list(configured_java_agent_transports())
    urls = os.environ.get(
        "AUTOMATION_HARNESS_JAVAFX_AGENT_URLS",
        os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_URL", ""),
    ).split(",")
    tokens = os.environ.get(
        "AUTOMATION_HARNESS_JAVAFX_AGENT_TOKENS",
        os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_TOKEN", ""),
    ).split(",")
    transports.extend(
        HttpJavaFxBridgeTransport(url.strip(), token.strip())
        for url, token in zip(urls, tokens)
        if url.strip() and token.strip()
    )
    return tuple({(item.endpoint, item.token): item for item in transports}.values())


def _identity_payload(identification):
    identity = dict(identification or {})
    mandatory = identity.get("mandatory", identity)
    assistive = identity.get("assistive", {})
    if not isinstance(mandatory, Mapping) or not isinstance(assistive, Mapping):
        raise ValueError("Java agent identification must be a mapping")
    payload = {}
    for key in ("name", "accessible_id", "native_class", "window", "component_path"):
        value = mandatory.get(key, assistive.get(key))
        if value not in (None, ""):
            payload[key] = str(value)
    if not payload:
        raise ValueError("Java agent identification requires stable name, accessible_id, native_class, or window evidence")
    return payload
