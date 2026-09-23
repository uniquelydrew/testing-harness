"""Execution boundary for deterministic, evidence-bearing object capture."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

from automation_harness.core.runtime_observation import RuntimeObservation
from automation_harness.core.technology_router import (
    AdapterKind,
    RoutingDecision,
    TargetContext,
    TechnologyRouter,
)
from automation_harness.models.component import CapturedComponent


class CaptureAdapter(Protocol):
    @property
    def available(self) -> bool: ...

    def capture_next_click(self, *, timeout: float) -> CapturedComponent: ...


@dataclass(frozen=True)
class RoutedCaptureResult:
    observation: RuntimeObservation
    decision: RoutingDecision


class RoutedCaptureService:
    """Capture with one preselected owner and an explicit fallback contract."""

    def __init__(
        self,
        adapters: Mapping[AdapterKind, CaptureAdapter],
        *,
        router: TechnologyRouter | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._router = router or TechnologyRouter()

    def capture(self, target: TargetContext, *, timeout: float = 30.0) -> RoutedCaptureResult:
        decision = self._router.route(
            target,
            availability={
                kind: self._available(adapter)
                for kind, adapter in self._adapters.items()
            },
        )
        try:
            captured = self._capture(decision.adapter, timeout)
        except Exception as primary_error:
            if decision.fallback is None:
                raise
            try:
                captured = self._capture(decision.fallback, timeout)
            except Exception as fallback_error:
                raise LookupError(
                    "%s capture failed (%s: %s); explicit %s fallback failed (%s: %s)"
                    % (
                        decision.adapter.value,
                        type(primary_error).__name__,
                        primary_error,
                        decision.fallback.value,
                        type(fallback_error).__name__,
                        fallback_error,
                    )
                ) from fallback_error
            observation = RuntimeObservation.from_capture(
                captured,
                adapter=decision.fallback.value,
                authoritative=False,
                reasons=decision.reasons + (
                    "authoritative adapter failed: %s: %s"
                    % (type(primary_error).__name__, primary_error),
                ),
                fallback_used=True,
            )
            return RoutedCaptureResult(observation, decision)

        observation = RuntimeObservation.from_capture(
            captured,
            adapter=decision.adapter.value,
            authoritative=True,
            reasons=decision.reasons,
        )
        self._validate_correlation(target, observation)
        return RoutedCaptureResult(observation, decision)

    def _capture(self, kind: AdapterKind, timeout: float) -> CapturedComponent:
        try:
            adapter = self._adapters[kind]
        except KeyError as exc:
            raise LookupError("capture adapter %s is not configured" % kind.value) from exc
        return adapter.capture_next_click(timeout=timeout)

    @staticmethod
    def _available(adapter: CaptureAdapter) -> bool:
        try:
            return bool(adapter.available)
        except Exception:
            return False

    @staticmethod
    def _validate_correlation(target: TargetContext, observation: RuntimeObservation) -> None:
        if (
            target.process_id is not None
            and observation.process_id is not None
            and observation.process_id != target.process_id
        ):
            raise LookupError(
                "capture resolved process %s but routed target belongs to process %s"
                % (observation.process_id, target.process_id)
            )
        if (
            target.window_id
            and observation.window_id
            and observation.window_id != target.window_id
        ):
            raise LookupError(
                "capture resolved window %r but routed target belongs to window %r"
                % (observation.window_id, target.window_id)
            )
