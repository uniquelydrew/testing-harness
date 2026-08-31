"""Lifecycle, correlation, repository matching, and plan conversion for recording."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Protocol

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import CapturedComponent, ComponentDefinition
from automation_harness.models.gui import ActionType
from automation_harness.models.plan import StepCall
from automation_harness.recording.evidence import parameters_for_pointer
from automation_harness.recording.observations import (
    ActionFired, FocusChanged, Observation, PointerInteraction, StateChanged, TextChanged,
)


@dataclass(frozen=True)
class StateDelta:
    component: CapturedComponent
    property: str
    before: Any
    after: Any


@dataclass(frozen=True)
class RepositoryMatch:
    status: str  # known_unique, ambiguous, new_candidate, unresolved
    component_ids: tuple[str, ...] = ()

    @property
    def component_id(self) -> str | None:
        return self.component_ids[0] if self.status == "known_unique" else None


@dataclass(frozen=True)
class RecordedInteraction:
    action: ActionType
    target: CapturedComponent | None
    parameters: Mapping[str, Any]
    started_at: float
    completed_at: float
    resulting_changes: tuple[StateDelta, ...] = ()
    evidence: Mapping[str, Any] = field(default_factory=dict)
    confidence: float = 1.0
    repository_match: RepositoryMatch = field(default_factory=lambda: RepositoryMatch("unresolved"))


class RecordingAdapter(Protocol):
    """An adapter must subscribe only while the session is active."""
    def start(self, emit: Callable[[Observation], None]) -> None: ...
    def stop(self) -> None: ...


_NOISE_STATE = frozenset({"pressed", "hover", "armed", "layout", "bounds", "css"})


class RecordingSession:
    """Correlate bounded observations into semantic authoring interactions."""

    def __init__(
        self,
        adapters: Iterable[RecordingAdapter] = (),
        *,
        repository: ComponentRepository | None = None,
        correlation_window: float = 0.75,
        diagnostics: bool = False,
        diagnostic_limit: int = 256,
    ) -> None:
        if correlation_window <= 0:
            raise ValueError("correlation_window must be positive")
        self.adapters = tuple(adapters)
        self.repository = repository
        self.correlation_window = correlation_window
        self.diagnostics = diagnostics
        self._diagnostics: deque[Observation] = deque(maxlen=diagnostic_limit)
        self._active = False
        self._interactions: list[RecordedInteraction] = []
        self._pending: RecordedInteraction | None = None

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> None:
        if self._active:
            raise RuntimeError("recording session is already active")
        self._active = True
        try:
            for adapter in self.adapters:
                adapter.start(self.observe)
        except Exception:
            for adapter in reversed(self.adapters):
                try:
                    adapter.stop()
                except Exception:
                    pass
            self._active = False
            raise

    def stop(self) -> tuple[RecordedInteraction, ...]:
        if not self._active:
            return tuple(self._interactions)
        try:
            # Adapters may return their final source-filtered batch from stop;
            # retain it for correlation before closing the session gate.
            for adapter in reversed(self.adapters):
                adapter.stop()
        finally:
            self._active = False
            self._flush()
        return tuple(self._interactions)

    def observations(self) -> tuple[Observation, ...]:
        """Diagnostics only; normal operation intentionally retains no raw stream."""
        return tuple(self._diagnostics)

    def interactions(self) -> tuple[RecordedInteraction, ...]:
        return tuple(self._interactions) + ((self._pending,) if self._pending else ())

    def observe(self, observation: Observation) -> None:
        if not self._active:
            return
        if self.diagnostics:
            self._diagnostics.append(observation)
        if isinstance(observation, PointerInteraction):
            if observation.phase != "released" or observation.target is None:
                return
            action = ActionType.RIGHT_CLICK if observation.button == "secondary" else ActionType.CLICK
            self._begin(action, observation, self._pointer_parameters(observation))
        elif isinstance(observation, TextChanged):
            if observation.target is not None and observation.after is not None:
                self._begin(ActionType.SET_TEXT, observation, {"value": observation.after})
        elif isinstance(observation, ActionFired):
            if observation.target is None:
                return
            if self._pending and _same_target(self._pending.target, observation.target):
                self._merge_action(observation)
            else:
                self._begin(_action_type(observation.action), observation, {})
        elif isinstance(observation, StateChanged):
            if observation.target is not None and observation.property not in _NOISE_STATE and observation.before != observation.after:
                self._add_delta(observation)
        elif isinstance(observation, FocusChanged):
            # Focus is correlation context only, never a standalone record.
            return

    def _begin(self, action: ActionType, observation: Observation, parameters: Mapping[str, Any]) -> None:
        if self._pending and (
            observation.timestamp - self._pending.completed_at > self.correlation_window
            or not _same_target(self._pending.target, observation.target)
        ):
            self._flush()
        if self._pending and action == ActionType.SET_TEXT and self._pending.action == ActionType.SET_TEXT and _same_target(self._pending.target, observation.target):
            self._pending = RecordedInteraction(
                action, observation.target, dict(parameters), self._pending.started_at, observation.timestamp,
                self._pending.resulting_changes, self._pending.evidence, self._pending.confidence,
                self._match(observation.target),
            )
            return
        if self._pending:
            self._flush()
        self._pending = RecordedInteraction(
            action, observation.target, dict(parameters), observation.timestamp, observation.timestamp,
            evidence=dict(observation.evidence), confidence=1.0 if observation.target else 0.4,
            repository_match=self._match(observation.target),
        )

    def _merge_action(self, observation: ActionFired) -> None:
        assert self._pending is not None
        action = _action_type(observation.action)
        # A direct control event is stronger evidence than a generic pointer
        # click for state-owning controls such as check boxes and lists.
        if action not in {ActionType.ACTIVATE, ActionType.CLICK}:
            selected_action = action
        else:
            selected_action = self._pending.action
        self._pending = RecordedInteraction(
            selected_action, self._pending.target, self._pending.parameters,
            self._pending.started_at, observation.timestamp, self._pending.resulting_changes,
            {**self._pending.evidence, **dict(observation.evidence), "action_fired": observation.action},
            self._pending.confidence, self._pending.repository_match,
        )

    def _add_delta(self, observation: StateChanged) -> None:
        if self._pending is None:
            return
        same_target = _same_target(self._pending.target, observation.target)
        contextual_transition = observation.property in {"visible", "showing", "active"} and bool(observation.after)
        if not same_target and not contextual_transition:
            return
        delta = StateDelta(observation.target, observation.property, observation.before, observation.after)
        action = self._pending.action
        if same_target and observation.property in {"checked", "selected"} and action == ActionType.CLICK:
            action = ActionType.TOGGLE if observation.property == "checked" else ActionType.SELECT
        self._pending = RecordedInteraction(
            action, self._pending.target, self._pending.parameters,
            self._pending.started_at, observation.timestamp, (*self._pending.resulting_changes, delta),
            self._pending.evidence, self._pending.confidence, self._pending.repository_match,
        )

    def _flush(self) -> None:
        if self._pending is not None:
            self._interactions.append(self._pending)
            self._pending = None

    def _match(self, target: CapturedComponent | None) -> RepositoryMatch:
        if target is None:
            return RepositoryMatch("unresolved")
        if self.repository is None:
            return RepositoryMatch("new_candidate")
        matches = [component_id for component_id, definition in self.repository.components.items() if _matches_capture(definition, target)]
        if len(matches) == 1:
            return RepositoryMatch("known_unique", tuple(matches))
        if len(matches) > 1:
            return RepositoryMatch("ambiguous", tuple(matches))
        return RepositoryMatch("new_candidate")

    @staticmethod
    def _pointer_parameters(observation: PointerInteraction) -> dict[str, Any]:
        action = ActionType.RIGHT_CLICK if observation.button == "secondary" else ActionType.CLICK
        return parameters_for_pointer(action, observation.target, observation.coordinates)


def interactions_to_steps(interactions: Iterable[RecordedInteraction], *, start_index: int = 1) -> tuple[StepCall, ...]:
    """Convert only reviewed, uniquely resolved interactions to normal GUI steps."""
    result: list[StepCall] = []
    for index, interaction in enumerate(interactions, start_index):
        component_id = interaction.repository_match.component_id
        if component_id is None:
            raise ValueError("recorded interaction must have a unique repository match before adding it to a test")
        action: dict[str, Any] = {"type": interaction.action.value}
        action.update(interaction.parameters)
        result.append(StepCall(
            node_id=f"recorded-{index:03d}", step_id="gui.object.action",
            inputs={"component_id": component_id, "action": action},
            description=f"Recorded {interaction.action.value} on {component_id}",
        ))
    return tuple(result)


def _same_target(left: CapturedComponent | None, right: CapturedComponent | None) -> bool:
    if left is None or right is None:
        return left is right
    return (left.framework, left.accessible_id, left.name, left.role, left.window) == (right.framework, right.accessible_id, right.name, right.role, right.window)


def _action_type(value: str) -> ActionType:
    normalized = value.casefold().replace(" ", "_")
    return {
        "click": ActionType.CLICK,
        "secondary_click": ActionType.RIGHT_CLICK,
        "right_click": ActionType.RIGHT_CLICK,
        "toggle": ActionType.TOGGLE,
        "select": ActionType.SELECT,
        "activate": ActionType.ACTIVATE,
        "fire": ActionType.ACTIVATE,
    }.get(normalized, ActionType.ACTIVATE)


def _matches_capture(definition: ComponentDefinition, capture: CapturedComponent) -> bool:
    if definition.framework and capture.framework and definition.framework != capture.framework:
        return False
    if definition.object_type != capture.semantic_type() and definition.object_type.value != "custom":
        return False
    for strategy in definition.strategies:
        identity = strategy.options.get("identification", {}) if isinstance(strategy.options, Mapping) else {}
        mandatory = identity.get("mandatory", identity) if isinstance(identity, Mapping) else {}
        if not isinstance(mandatory, Mapping):
            continue
        supported = {key: value for key, value in mandatory.items() if key in {"name", "role", "accessible_id", "application", "window"}}
        if supported and all(getattr(capture, key, None) == value for key, value in supported.items()):
            return True
    return False
