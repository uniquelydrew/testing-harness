"""Deterministic technology routing for object capture.

The router selects one authoritative adapter from positive process/toolkit
evidence.  It deliberately does not race capture backends: accessibility is a
fallback for an instrumented Java target only after an explicit native failure.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from automation_harness.core.runtime_observation import Framework


class AdapterKind(str, Enum):
    JAVA_AGENT = "java_agent"
    JAVAFX = "javafx"
    ATSPI = "atspi"
    RENDERED = "rendered"


@dataclass(frozen=True)
class TargetContext:
    process_id: int | None
    window_id: str | None
    application: str | None = None
    framework_hint: Framework = Framework.UNKNOWN
    java_agent_pids: frozenset[int] = frozenset()
    javafx_pids: frozenset[int] = frozenset()
    rendered_surface: bool = False


@dataclass(frozen=True)
class RoutingDecision:
    adapter: AdapterKind
    framework: Framework
    authoritative: bool
    fallback: AdapterKind | None
    reasons: tuple[str, ...]


class TechnologyRoutingError(LookupError):
    pass


class TechnologyRouter:
    """Choose capture ownership before an adapter is allowed to observe input."""

    def route(
        self,
        target: TargetContext,
        *,
        availability: Mapping[AdapterKind, bool],
    ) -> RoutingDecision:
        pid = target.process_id
        if target.rendered_surface or target.framework_hint is Framework.RENDERED:
            return self._require(
                AdapterKind.RENDERED,
                Framework.RENDERED,
                availability,
                reasons=("target is a registered rendering surface",),
                fallback=None,
            )

        # A JavaFX endpoint is authoritative even when the process also hosts
        # an AWT/Swing agent (for example an MSCT shell with a JFXPanel).
        # Falling back to the canvas loses the JavaFX semantic target.
        if pid is not None and pid in target.javafx_pids:
            return self._require(
                AdapterKind.JAVAFX,
                Framework.JAVAFX,
                availability,
                reasons=("owning process has an active JavaFX endpoint",),
                fallback=None,
            )

        if pid is not None and pid in target.java_agent_pids:
            framework = target.framework_hint
            if framework not in {Framework.SWING, Framework.AWT, Framework.JAVAFX}:
                framework = Framework.UNKNOWN
            return self._require(
                AdapterKind.JAVA_AGENT,
                framework,
                availability,
                reasons=("owning process has an active Java agent",),
                # Native Swing/AWT failure may use AT-SPI only as an explicit
                # degraded route. JavaFX and rendered targets never inherit
                # this fallback.
                fallback=AdapterKind.ATSPI if framework in {Framework.SWING, Framework.AWT} else None,
            )

        if target.framework_hint is Framework.JAVAFX:
            raise TechnologyRoutingError(
                "JavaFX target has no native JavaFX endpoint; AT-SPI substitution is prohibited"
            )

        return self._require(
            AdapterKind.ATSPI,
            target.framework_hint if target.framework_hint is not Framework.UNKNOWN else Framework.ATSPI,
            availability,
            reasons=("target process has no native automation endpoint",),
            fallback=None,
        )

    @staticmethod
    def _require(adapter, framework, availability, *, reasons, fallback):
        if not availability.get(adapter, False):
            raise TechnologyRoutingError(
                "authoritative %s adapter is unavailable" % adapter.value
            )
        if fallback is not None and not availability.get(fallback, False):
            fallback = None
        return RoutingDecision(
            adapter=adapter,
            framework=framework,
            authoritative=True,
            fallback=fallback,
            reasons=tuple(reasons),
        )
