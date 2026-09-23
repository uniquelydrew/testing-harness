"""Backend-neutral runtime evidence produced before repository materialization.

Capture adapters describe what they observed; they do not decide what becomes a
persistent test object.  Keeping this boundary explicit prevents recording,
direct capture, and maintenance from developing different semantic models.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from automation_harness.models.component import CapturedComponent, ComponentState


class Framework(str, Enum):
    ATSPI = "atspi"
    SWING = "swing"
    AWT = "awt"
    JAVAFX = "javafx"
    RENDERED = "rendered"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RuntimeAncestor:
    native_class: str | None = None
    role: str | None = None
    name: str | None = None
    stable_id: str | None = None


@dataclass(frozen=True)
class CaptureEvidence:
    adapter: str
    authoritative: bool
    reasons: tuple[str, ...] = ()
    fallback_used: bool = False


@dataclass(frozen=True)
class RuntimeObservation:
    framework: Framework
    process_id: int | None
    window_id: str | None
    physical_class: str | None
    semantic_role: str | None
    name: str | None
    description: str | None
    accessible_id: str | None
    application: str | None
    properties: Mapping[str, Any]
    state: ComponentState
    bounds: tuple[int, int, int, int] | None
    physical_ancestry: tuple[RuntimeAncestor, ...] = ()
    capabilities: frozenset[str] = frozenset()
    logical_children: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    evidence: CaptureEvidence = field(
        default_factory=lambda: CaptureEvidence("unknown", False)
    )

    @classmethod
    def from_capture(
        cls,
        captured: CapturedComponent,
        *,
        adapter: str,
        authoritative: bool,
        reasons: tuple[str, ...] = (),
        fallback_used: bool = False,
    ) -> "RuntimeObservation":
        properties = dict(captured.backend_properties or {})
        process_id = _positive_int(
            properties.get("process_id", properties.get("bridge_pid", properties.get("pid")))
        )
        ancestry = tuple(
            RuntimeAncestor(name=str(item)) for item in captured.hierarchy if str(item)
        )
        return cls(
            framework=framework_for_capture(captured, adapter=adapter),
            process_id=process_id,
            window_id=captured.window,
            physical_class=captured.native_class,
            semantic_role=captured.role,
            name=captured.name,
            description=captured.description,
            accessible_id=captured.accessible_id,
            application=captured.application,
            properties=properties,
            state=captured.state,
            bounds=captured.bounds,
            physical_ancestry=ancestry,
            capabilities=frozenset(str(item) for item in captured.actions),
            logical_children={
                str(key): dict(value)
                for key, value in captured.logical_subobjects.items()
            },
            evidence=CaptureEvidence(
                adapter=adapter,
                authoritative=authoritative,
                reasons=tuple(reasons),
                fallback_used=fallback_used,
            ),
        )


def framework_for_capture(captured: CapturedComponent, *, adapter: str) -> Framework:
    value = str(captured.framework or "").casefold()
    native = str(captured.native_class or "").casefold()
    adapter_value = str(adapter).casefold()
    if value == "solipsys_rendered" or "rendered" in value:
        return Framework.RENDERED
    if value == "javafx" or native.startswith("javafx.") or "javafx" in adapter_value:
        return Framework.JAVAFX
    if value == "swing" or native.startswith("javax.swing."):
        return Framework.SWING
    if value == "awt" or native.startswith("java.awt."):
        return Framework.AWT
    if adapter_value == "atspi" or value in {"atspi", "gtk"}:
        return Framework.ATSPI
    return Framework.UNKNOWN


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None
