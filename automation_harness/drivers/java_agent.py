"""Client for the opt-in mixed Swing/JavaFX in-process HTTP agent."""
from __future__ import annotations

import os
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
        self.transports = _configured_transports()

    @property
    def available(self) -> bool:
        return bool(self.transports)

    def capture_at_point(self, x: int, y: int) -> CapturedComponent:
        return self._first("hit_test", {"x": int(x), "y": int(y)})

    def capture_next_click(self, *, timeout: float = 30.0) -> CapturedComponent:
        return self._first("capture_next_click", {"timeout": float(timeout)})

    def inspect(self, *, identification: Mapping[str, Any] | None = None, **_kwargs) -> CapturedComponent:
        return self._first("resolve", _identity_payload(identification))

    def resolve(self, component_id: str, *, identification=None, **kwargs) -> ResolvedComponent:
        captured = self.inspect(identification=identification, **kwargs)
        metadata = captured.to_dict()
        metadata["bounds"] = list(captured.bounds) if captured.bounds else None
        return ResolvedComponent(component_id, "java_agent", metadata)

    def state(self, *, identification=None, **kwargs):
        return self.inspect(identification=identification, **kwargs).state

    def activate(self, *, identification=None, **_kwargs):
        captured = self._first("activate", _identity_payload(identification))
        return {"action": "activate", "component": captured.to_dict()}

    def _first(self, operation: str, payload: Mapping[str, Any]) -> CapturedComponent:
        if not self.transports:
            raise JavaAgentUnavailable("no configured Automation Harness Java agent endpoint")
        errors = []
        for transport in self.transports:
            try:
                response = transport.request(operation, dict(payload))
                node = response.get("semantic_node", response.get("node"))
                if not isinstance(node, Mapping):
                    raise ValueError("Java agent response contains no semantic node")
                return _captured_recording_node(node)
            except Exception as exc:
                errors.append("%s: %s" % (type(exc).__name__, exc))
        raise JavaAgentUnavailable("all configured Java agents failed: " + "; ".join(errors))


def _configured_transports():
    urls = os.environ.get(
        "AUTOMATION_HARNESS_JAVA_AGENT_URLS",
        os.environ.get("AUTOMATION_HARNESS_JAVA_AGENT_URL", os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_URL", "")),
    ).split(",")
    tokens = os.environ.get(
        "AUTOMATION_HARNESS_JAVA_AGENT_TOKENS",
        os.environ.get("AUTOMATION_HARNESS_JAVA_AGENT_TOKEN", os.environ.get("AUTOMATION_HARNESS_JAVAFX_AGENT_TOKEN", "")),
    ).split(",")
    return tuple(
        HttpJavaFxBridgeTransport(url.strip(), token.strip())
        for url, token in zip(urls, tokens)
        if url.strip() and token.strip()
    )


def _identity_payload(identification):
    identity = dict(identification or {})
    mandatory = identity.get("mandatory", identity)
    assistive = identity.get("assistive", {})
    if not isinstance(mandatory, Mapping) or not isinstance(assistive, Mapping):
        raise ValueError("Java agent identification must be a mapping")
    payload = {}
    for key in ("name", "accessible_id", "native_class", "window"):
        value = mandatory.get(key, assistive.get(key))
        if value not in (None, ""):
            payload[key] = str(value)
    if not payload:
        raise ValueError("Java agent identification requires stable name, accessible_id, native_class, or window evidence")
    return payload
