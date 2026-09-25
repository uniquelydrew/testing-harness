"""Lifecycle, correlation, repository matching, and plan conversion for recording."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, replace
import threading
from typing import Any, Callable, Iterable, Mapping, Protocol

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.logical_menu import find_logical_menu_targets, is_javafx_menu_skin_capture
from automation_harness.core.locator_matching import _javafx_node_matches
from automation_harness.models.component import CapturedComponent, ComponentDefinition
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.models.plan import StepCall
from automation_harness.recording.evidence import parameters_for_pointer
from automation_harness.recording.diagnostics import RecordingDebugLog
from automation_harness.recording.observations import (
    ActionFired, FocusChanged, KeyboardInput, Observation, PointerInteraction,
    StateChanged, TextChanged, WindowChanged,
)


@dataclass(frozen=True)
class StateDelta:
    component: CapturedComponent
    property: str
    before: Any
    after: Any


@dataclass(frozen=True)
class RepositoryMatch:
    status: str  # known_unique, known_subobject, ambiguous, new_candidate, unresolved
    component_ids: tuple[str, ...] = ()
    subobject_path: tuple[str, ...] = ()

    @property
    def component_id(self) -> str | None:
        if self.status in {"known_unique", "known_subobject"} and len(self.component_ids) == 1:
            return self.component_ids[0]
        return None


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


@dataclass
class MenuRecordingContext:
    """One semantic menu traversal; intermediate physical input is evidence only."""
    owner: CapturedComponent
    started_at: float
    window: str | None
    last_target: CapturedComponent | None = None
    invoking_target: CapturedComponent | None = None


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
        debug_log: RecordingDebugLog | None = None,
    ) -> None:
        if correlation_window <= 0:
            raise ValueError("correlation_window must be positive")
        self.adapters = tuple(adapters)
        self.repository = repository
        self.correlation_window = correlation_window
        self.diagnostics = diagnostics
        self.debug_log = debug_log
        self._diagnostics: deque[Observation] = deque(maxlen=diagnostic_limit)
        self._active = False
        self._interactions: list[RecordedInteraction] = []
        self._pending: RecordedInteraction | None = None
        self._menu_context: MenuRecordingContext | None = None
        self._captured_menu_owners: list[CapturedComponent] = []
        # Adapter callbacks arrive on independent AT-SPI and JavaFX threads.
        # Correlation is stateful, so every observation and lifecycle snapshot
        # must be serialized as one transaction.
        self._lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self.diagnostic(
            "session_created",
            correlation_window=correlation_window,
            diagnostics=diagnostics,
            adapters=[_adapter_details(item) for item in self.adapters],
            repository=self.repository.to_document() if self.repository is not None else None,
        )

    @property
    def diagnostic_path(self):
        return self.debug_log.path if self.debug_log is not None else None

    def diagnostic(self, event: str, **payload: Any) -> None:
        if self.debug_log is not None:
            self.debug_log.write(event, **payload)

    def diagnostic_exception(self, event: str, error: BaseException, **payload: Any) -> None:
        if self.debug_log is not None:
            self.debug_log.exception(event, error, **payload)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._active

    def start(self) -> None:
        with self._lifecycle_lock:
            with self._lock:
                if self._active:
                    raise RuntimeError("recording session is already active")
                self._active = True
                self.diagnostic("session_starting")
            try:
                for adapter in self.adapters:
                    setter = getattr(adapter, "set_diagnostic_sink", None)
                    if callable(setter):
                        setter(lambda event, _adapter=adapter, **data: self.diagnostic(
                            "adapter.%s" % event,
                            adapter=_adapter_details(_adapter),
                            **data
                        ))
                    self.diagnostic("adapter_starting", adapter=_adapter_details(adapter))
                    adapter.start(self.observe)
                    self.diagnostic("adapter_started", adapter=_adapter_details(adapter))
            except Exception as exc:
                self.diagnostic_exception("session_start_failed", exc)
                for adapter in reversed(self.adapters):
                    try:
                        adapter.stop()
                    except Exception as stop_exc:
                        self.diagnostic_exception("adapter_cleanup_failed", stop_exc, adapter=_adapter_details(adapter))
                with self._lock:
                    self._active = False
                raise

    def stop(self) -> tuple[RecordedInteraction, ...]:
        with self._lifecycle_lock:
            with self._lock:
                if not self._active:
                    return tuple(self._interactions)
            try:
                for adapter in reversed(self.adapters):
                    self.diagnostic("adapter_stopping", adapter=_adapter_details(adapter))
                    try:
                        adapter.stop()
                    except Exception as exc:
                        self.diagnostic_exception("adapter_stop_failed", exc, adapter=_adapter_details(adapter))
                        raise
                    self.diagnostic("adapter_stopped", adapter=_adapter_details(adapter))
            finally:
                with self._lock:
                    self._active = False
                    if self._menu_context is not None:
                        self._finish_menu_context("recording_stopped", cancelled=True)
                    self._flush()
                    result = tuple(self._interactions)
                    self.diagnostic("session_stopped", interactions=result)
            return result

    def observations(self) -> tuple[Observation, ...]:
        with self._lock:
            return tuple(self._diagnostics)

    def interactions(self) -> tuple[RecordedInteraction, ...]:
        with self._lock:
            return tuple(self._interactions) + ((self._pending,) if self._pending else ())

    def captured_menu_owners(self) -> tuple[CapturedComponent, ...]:
        with self._lock:
            return tuple(self._captured_menu_owners)

    def observe(self, observation: Observation) -> None:
        with self._lock:
            self.diagnostic("observation_received", observation=observation, active=self._active)
            if not self._active:
                self.diagnostic("observation_ignored", reason="session_inactive", observation=observation)
                return
            if self.diagnostics:
                self._diagnostics.append(observation)

            if isinstance(observation, PointerInteraction):
                if observation.target is not None and observation.target.framework == "solipsys_rendered":
                    self.diagnostic(
                        "solipsys_semantic_capture",
                        **_solipsys_diagnostic_summary(observation.target),
                    )
                if observation.phase != "released" or observation.target is None:
                    self.diagnostic(
                        "observation_ignored", reason="pointer_not_released_or_missing_target",
                        observation=observation,
                    )
                    return

                target = observation.target
                action = ActionType.RIGHT_CLICK if observation.button == "secondary" else ActionType.CLICK
                combo_selection = dict(target.backend_properties or {}).get("combo_selection")
                if isinstance(combo_selection, Mapping) and target.semantic_type() == ObjectType.COMBO_BOX:
                    index = combo_selection.get("index")
                    if isinstance(index, int) and not isinstance(index, bool) and index >= 0:
                        self._begin(ActionType.SELECT_ITEM, observation, {"value": index})
                        return
                if self._menu_context is not None:
                    if _is_menu_related_capture(target):
                        self._menu_context.last_target = target
                        if _is_terminal_menu_capture(target):
                            self._begin(action, observation, self._pointer_parameters(observation))
                            self._attach_menu_context_to_pending()
                            self._finish_menu_context("terminal_pointer_selection")
                        else:
                            self.diagnostic(
                                "menu_traversal_observation_suppressed",
                                observation=observation,
                                owner=self._menu_context.owner,
                            )
                        return
                    self._finish_menu_context("pointer_left_menu_scope", cancelled=True)

                if _is_menu_owner_capture(target):
                    self._start_menu_context(target, observation.timestamp, "menu_pointer_open")
                    return

                if target.semantic_type() == ObjectType.COMBO_BOX and action == ActionType.CLICK:
                    self.diagnostic("combo_popup_opener_suppressed", owner=target)
                    return

                self._begin(action, observation, self._pointer_parameters(observation))
                return

            if isinstance(observation, TextChanged):
                if observation.target is not None and observation.after is not None:
                    self._begin(ActionType.SET_TEXT, observation, {"value": observation.after})
                return

            if isinstance(observation, ActionFired):
                if observation.target is None:
                    return
                target = observation.target
                if (
                    target.semantic_type() == ObjectType.COMBO_BOX
                    and self._pending is not None
                    and self._pending.action == ActionType.SELECT_ITEM
                    and _same_logical_target(self._pending.target, target)
                ):
                    return
                if self._menu_context is not None and _is_menu_related_capture(target):
                    self._menu_context.last_target = target
                    if _is_terminal_menu_capture(target):
                        if self._pending and _same_logical_target(self._pending.target, target):
                            self._merge_action(observation)
                        else:
                            self._begin(_action_type(observation.action), observation, {})
                        self._attach_menu_context_to_pending()
                        self._finish_menu_context("terminal_menu_action")
                    else:
                        self.diagnostic(
                            "menu_traversal_action_suppressed",
                            observation=observation,
                            owner=self._menu_context.owner,
                        )
                    return
                if self._pending and _same_logical_target(self._pending.target, target):
                    self._merge_action(observation)
                else:
                    self._begin(_action_type(observation.action), observation, {})
                return

            if isinstance(observation, StateChanged):
                target = observation.target
                if (
                    target is not None
                    and observation.property in {"visible", "showing", "expanded", "active"}
                    and observation.before != observation.after
                ):
                    if bool(observation.after) and _is_menu_owner_capture(target):
                        self._start_menu_context(target, observation.timestamp, "menu_became_active")
                        return
                    if (
                        self._menu_context is not None
                        and not bool(observation.after)
                        and _is_menu_related_capture(target)
                    ):
                        self._finish_menu_context("menu_became_inactive", cancelled=self._pending is None)
                        return
                if target is not None and observation.property not in _NOISE_STATE and observation.before != observation.after:
                    self._add_delta(observation)
                return

            if isinstance(observation, KeyboardInput):
                if (
                    self._menu_context is not None
                    and observation.phase == "pressed"
                    and str(observation.key).casefold() in {"escape", "esc"}
                ):
                    self._finish_menu_context("escape", cancelled=True)
                    return
                self.diagnostic("observation_ignored", reason="keyboard_not_semantic_action", observation=observation)
                return

            if isinstance(observation, WindowChanged):
                if self._menu_context is not None:
                    owner_window = self._menu_context.window
                    changed_window = observation.window
                    if changed_window and owner_window and changed_window != owner_window:
                        self._finish_menu_context("top_level_window_changed", cancelled=self._pending is None)
                self.diagnostic("observation_ignored", reason="window_change", observation=observation)
                return

            if isinstance(observation, FocusChanged):
                self.diagnostic("observation_ignored", reason="focus_change", observation=observation)
                return

    def _attach_menu_context_to_pending(self) -> None:
        context = self._menu_context
        pending = self._pending
        if context is None or pending is None:
            return
        evidence = {
            **dict(pending.evidence),
            "menu_owner_capture": context.owner,
            "menu_transaction_started_at": context.started_at,
        }
        if context.invoking_target is not None:
            evidence["menu_invoking_capture"] = context.invoking_target
        self._pending = replace(
            pending,
            evidence=evidence,
        )

    def _start_menu_context(self, owner: CapturedComponent, timestamp: float, reason: str) -> None:
        if not any(_same_logical_target(existing, owner) for existing in self._captured_menu_owners):
            self._captured_menu_owners.append(owner)
        if self._menu_context is not None:
            if _same_logical_target(self._menu_context.owner, owner):
                self._menu_context.last_target = owner
                return
            self._finish_menu_context("menu_owner_changed", cancelled=True)

        # A context menu is commonly preceded by a right click on its invoking
        # object. Once the popup becomes active that physical opener is evidence
        # for the menu transaction, not an independent authored step.
        absorbed = None
        if (
            self._pending is not None
            and self._pending.action in {ActionType.CLICK, ActionType.RIGHT_CLICK}
            and timestamp - self._pending.completed_at <= self.correlation_window
        ):
            absorbed = self._pending
            self._pending = None

        self._menu_context = MenuRecordingContext(
            owner=owner,
            started_at=timestamp,
            window=owner.window or owner.application,
            last_target=owner,
            invoking_target=absorbed.target if absorbed is not None else None,
        )
        self.diagnostic(
            "menu_transaction_started",
            reason=reason,
            owner=owner,
            absorbed_opener=absorbed,
        )

    def _finish_menu_context(self, reason: str, *, cancelled: bool = False) -> None:
        context = self._menu_context
        if context is None:
            return
        self._menu_context = None
        self.diagnostic(
            "menu_transaction_finished",
            reason=reason,
            cancelled=cancelled,
            owner=context.owner,
            last_target=context.last_target,
            pending=self._pending,
        )

    def _begin(self, action: ActionType, observation: Observation, parameters: Mapping[str, Any]) -> None:
        self.diagnostic(
            "interaction_begin_evaluated", action=action, observation=observation,
            parameters=parameters, pending=self._pending,
        )
        if (
            self._pending
            and action in {ActionType.CLICK, ActionType.RIGHT_CLICK, ActionType.SELECT_ITEM}
            and self._pending.action == action
            and (action != ActionType.SELECT_ITEM or self._pending.parameters == parameters)
            and abs(observation.timestamp - self._pending.completed_at) <= min(self.correlation_window, 0.2)
            and _same_logical_target(self._pending.target, observation.target)
        ):
            target = _preferred_target(self._pending.target, observation.target)
            self._pending = RecordedInteraction(
                action, target, dict(parameters) if target is observation.target else self._pending.parameters,
                self._pending.started_at, max(self._pending.completed_at, observation.timestamp),
                self._pending.resulting_changes,
                {
                    **dict(self._pending.evidence),
                    **dict(observation.evidence),
                    "correlated_sources": sorted({
                        str(self._pending.target.framework if self._pending.target else ""),
                        str(observation.target.framework if observation.target else ""),
                    }),
                },
                max(self._pending.confidence, 1.0 if target else 0.4),
                self._match(target),
            )
            self.diagnostic("interaction_sources_correlated", interaction=self._pending)
            return
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
            self.diagnostic("text_interaction_coalesced", interaction=self._pending)
            return
        if self._pending:
            self._flush()
        self._pending = RecordedInteraction(
            action, observation.target, dict(parameters), observation.timestamp, observation.timestamp,
            evidence=dict(observation.evidence), confidence=1.0 if observation.target else 0.4,
            repository_match=self._match(observation.target),
        )
        self.diagnostic("interaction_pending", interaction=self._pending)

    def _merge_action(self, observation: ActionFired) -> None:
        assert self._pending is not None
        action = _action_type(observation.action)
        if action not in {ActionType.ACTIVATE, ActionType.CLICK}:
            selected_action = action
        else:
            selected_action = self._pending.action
        target = _preferred_target(self._pending.target, observation.target)
        self._pending = RecordedInteraction(
            selected_action, target, self._pending.parameters,
            self._pending.started_at, observation.timestamp, self._pending.resulting_changes,
            {**self._pending.evidence, **dict(observation.evidence), "action_fired": observation.action},
            self._pending.confidence, self._match(target),
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
            self.diagnostic("interaction_flushed", interaction=self._pending)
            self._interactions.append(self._pending)
            self._pending = None

    def _match(self, target: CapturedComponent | None) -> RepositoryMatch:
        if target is None:
            result = RepositoryMatch("unresolved")
            self.diagnostic("repository_match", target=None, result=result, reason="missing_target")
            return result
        if self.repository is None:
            result = RepositoryMatch("new_candidate")
            self.diagnostic("repository_match", target=target, result=result, reason="no_repository")
            return result
        if _is_terminal_menu_capture(target):
            logical = find_logical_menu_targets(self.repository.components.values(), target)
            if len(logical) == 1:
                match = logical[0]
                return RepositoryMatch("known_subobject", (match.owner_component_id,), match.subobject_path)
            if len(logical) > 1:
                owners = tuple(dict.fromkeys(item.owner_component_id for item in logical))
                return RepositoryMatch("ambiguous", owners)
        evaluations = []
        matches = []
        for component_id, definition in self.repository.components.items():
            matched, details = _matches_capture_details(definition, target)
            evaluations.append({"component_id": component_id, "matched": matched, "details": details})
            if matched:
                matches.append(component_id)
        if len(matches) == 1:
            result = RepositoryMatch("known_unique", tuple(matches))
            self.diagnostic("repository_match", target=target, result=result, evaluations=evaluations)
            return result
        if len(matches) > 1:
            result = RepositoryMatch("ambiguous", tuple(matches))
            self.diagnostic("repository_match", target=target, result=result, evaluations=evaluations)
            return result
        result = RepositoryMatch("new_candidate")
        self.diagnostic("repository_match", target=target, result=result, evaluations=evaluations)
        return result

    @staticmethod
    def _pointer_parameters(observation: PointerInteraction) -> dict[str, Any]:
        action = ActionType.RIGHT_CLICK if observation.button == "secondary" else ActionType.CLICK
        return parameters_for_pointer(action, observation.target, observation.coordinates)


_MENU_OWNER_TYPES = frozenset({
    ObjectType.MENU_BAR,
    ObjectType.MENU,
    ObjectType.CONTEXT_MENU,
})
_MENU_TERMINAL_TYPES = frozenset({
    ObjectType.MENU_ITEM,
    ObjectType.CHECK_MENU_ITEM,
    ObjectType.RADIO_MENU_ITEM,
})


def _is_menu_owner_capture(target: CapturedComponent | None) -> bool:
    return target is not None and target.semantic_type() in _MENU_OWNER_TYPES


def _is_menu_related_capture(target: CapturedComponent | None) -> bool:
    if target is None:
        return False
    if target.semantic_type() in (_MENU_OWNER_TYPES | _MENU_TERMINAL_TYPES):
        return True
    if is_javafx_menu_skin_capture(target):
        return True
    properties = dict(target.backend_properties or {})
    return isinstance(properties.get("logical_menu"), Mapping)


def _is_terminal_menu_capture(target: CapturedComponent | None) -> bool:
    if target is None:
        return False
    if target.semantic_type() in _MENU_TERMINAL_TYPES:
        return True
    properties = dict(target.backend_properties or {})
    metadata = properties.get("logical_menu")
    if not isinstance(metadata, Mapping):
        return False
    path = metadata.get("path")
    if not isinstance(path, (list, tuple)) or not path:
        return False
    last = path[-1]
    if not isinstance(last, Mapping):
        return False
    kind = str(last.get("kind") or "").replace("_", " ").casefold()
    return kind in {"menu item", "check menu item", "radio menu item"}



def interactions_to_steps(interactions: Iterable[RecordedInteraction], *, start_index: int = 1) -> tuple[StepCall, ...]:
    result: list[StepCall] = []
    for index, interaction in enumerate(interactions, start_index):
        component_id = interaction.repository_match.component_id
        if component_id is None:
            raise ValueError("recorded interaction must have a unique repository match before adding it to a test")
        if interaction.repository_match.status == "known_subobject":
            if not interaction.repository_match.subobject_path:
                raise ValueError("recorded menu subobject match has no owner-relative path")
            navigation = dict(interaction.evidence or {}).get("menu_navigation")
            if not isinstance(navigation, str) or not navigation.strip():
                navigation = " > ".join(interaction.repository_match.subobject_path)
            action: dict[str, Any] = {
                "type": ActionType.SELECT_MENU_ITEM.value,
                "path": list(interaction.repository_match.subobject_path),
            }
            description = "Recorded select_menu_item on %s -> %s" % (
                component_id, navigation,
            )
        else:
            action = {"type": interaction.action.value}
            action.update(interaction.parameters)
            description = f"Recorded {interaction.action.value} on {component_id}"
        result.append(StepCall(
            node_id=f"recorded-{index:03d}", step_id="gui.object.action",
            inputs={"component_id": component_id, "action": action},
            description=description,
        ))
    return tuple(result)


def _same_target(left: CapturedComponent | None, right: CapturedComponent | None) -> bool:
    if left is None or right is None:
        return left is right
    if left.framework == right.framework == "javafx":
        left_ref = _javafx_runtime_ref(left)
        right_ref = _javafx_runtime_ref(right)
        if left_ref is not None and right_ref is not None:
            return left_ref == right_ref
        left_identity = _javafx_capture_identity(left)
        right_identity = _javafx_capture_identity(right)
        if left_identity is not None and right_identity is not None:
            return left_identity == right_identity
    if left.framework == right.framework == "solipsys_rendered":
        return _same_solipsys_target(left, right)
    return (left.framework, left.accessible_id, left.name, left.role, left.window) == (right.framework, right.accessible_id, right.name, right.role, right.window)


def _same_logical_target(left: CapturedComponent | None, right: CapturedComponent | None) -> bool:
    if left is None or right is None:
        return left is right
    left_scope = (left.window or left.application or "").casefold()
    right_scope = (right.window or right.application or "").casefold()
    if left.framework == right.framework == "solipsys_rendered":
        return _same_solipsys_target(left, right)
    if (
        left.framework == right.framework == "javafx"
        and not (is_javafx_menu_skin_capture(left) and is_javafx_menu_skin_capture(right))
    ):
        return _same_target(left, right)
    if left.accessible_id and right.accessible_id:
        return (
            left.accessible_id == right.accessible_id
            and left.semantic_type() == right.semantic_type()
            and (not left_scope or not right_scope or left_scope == right_scope)
        )
    return (
        bool(left.name and right.name and left.name.casefold() == right.name.casefold())
        and left.semantic_type() == right.semantic_type()
        and (not left_scope or not right_scope or left_scope == right_scope)
    )


def _preferred_target(left: CapturedComponent | None, right: CapturedComponent | None) -> CapturedComponent | None:
    if right is not None and right.framework == "javafx":
        return right
    return left if left is not None else right


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
    return _matches_capture_details(definition, capture)[0]


def _matches_capture_details(definition: ComponentDefinition, capture: CapturedComponent):
    details = []
    if definition.framework and capture.framework and definition.framework != capture.framework:
        return False, [{"stage": "framework", "expected": definition.framework, "actual": capture.framework, "matched": False}]
    if definition.object_type != capture.semantic_type() and definition.object_type.value != "custom":
        return False, [{"stage": "object_type", "expected": definition.object_type.value, "actual": capture.semantic_type().value, "matched": False}]
    for strategy in definition.strategies:
        identity = strategy.options.get("identification", {}) if isinstance(strategy.options, Mapping) else {}
        if strategy.type == "java_agent" and capture.framework == "solipsys_rendered":
            matched, solipsys_details = _matches_solipsys_capture(identity, capture)
            details.append({"strategy": strategy.type, "matched": matched, **solipsys_details})
            if matched:
                return True, details
            continue
        if strategy.type == "javafx" and capture.framework == "javafx":
            matched, javafx_details = _matches_javafx_capture(identity, capture)
            details.append({"strategy": strategy.type, "matched": matched, **javafx_details})
            if matched:
                return True, details
            continue
        mandatory = identity.get("mandatory", identity) if isinstance(identity, Mapping) else {}
        if not isinstance(mandatory, Mapping):
            details.append({"strategy": strategy.type, "matched": False, "reason": "mandatory_not_mapping"})
            continue

        # Native/custom backends (notably Solipsys rendered objects) author
        # durable locator keys that are intentionally not CapturedComponent
        # attributes. Compare those keys against the capture's own authored
        # strategy instead of discarding them as unsupported.
        capture_strategy = capture.candidate_strategy()
        capture_identity = (
            capture_strategy.options.get("identification", {})
            if capture_strategy.type == strategy.type and isinstance(capture_strategy.options, Mapping)
            else {}
        )
        capture_mandatory = (
            capture_identity.get("mandatory", capture_identity)
            if isinstance(capture_identity, Mapping)
            else {}
        )
        capture_assistive = (
            capture_identity.get("assistive", {})
            if isinstance(capture_identity, Mapping)
            else {}
        )
        authored_assistive = (
            identity.get("assistive", {})
            if isinstance(identity, Mapping)
            else {}
        )
        if (
            capture_strategy.type == strategy.type
            and isinstance(capture_mandatory, Mapping)
            and mandatory
        ):
            mandatory_comparisons = {
                key: {
                    "expected": value,
                    "actual": capture_mandatory.get(key),
                    "matched": key in capture_mandatory and capture_mandatory.get(key) == value,
                }
                for key, value in mandatory.items()
            }
            assistive_comparisons = {
                key: {
                    "expected": value,
                    "actual": capture_assistive.get(key) if isinstance(capture_assistive, Mapping) else None,
                    "matched": (
                        isinstance(capture_assistive, Mapping)
                        and key in capture_assistive
                        and capture_assistive.get(key) == value
                    ),
                }
                for key, value in authored_assistive.items()
            } if isinstance(authored_assistive, Mapping) else {}
            matched = (
                all(item["matched"] for item in mandatory_comparisons.values())
                and all(item["matched"] for item in assistive_comparisons.values())
            )
            details.append({
                "strategy": strategy.type,
                "mandatory": dict(mandatory),
                "comparisons": mandatory_comparisons,
                "assistive_comparisons": assistive_comparisons,
                "matched": matched,
                "unsupported_mandatory_keys": [],
            })
            if matched:
                return True, details
            continue

        supported = {key: value for key, value in mandatory.items() if key in {"name", "role", "accessible_id", "application", "window"}}
        comparisons = {
            key: {"expected": value, "actual": getattr(capture, key, None), "matched": getattr(capture, key, None) == value}
            for key, value in supported.items()
        }
        matched = bool(supported) and all(item["matched"] for item in comparisons.values())
        details.append({
            "strategy": strategy.type, "mandatory": dict(mandatory),
            "supported": supported, "comparisons": comparisons, "matched": matched,
            "unsupported_mandatory_keys": sorted(set(mandatory) - set(supported)),
        })
        if matched:
            return True, details
    return False, details


def _solipsys_capture_locator(capture):
    from automation_harness.core.solipsys_identity import strategy_parts
    strategy = capture.candidate_strategy()
    if strategy.type != "java_agent":
        return {}, {}
    return strategy_parts(strategy.options)


def _same_solipsys_target(left, right):
    from automation_harness.core.solipsys_identity import locators_match, runtime_correlation_key
    left_mandatory, left_assistive = _solipsys_capture_locator(left)
    right_mandatory, right_assistive = _solipsys_capture_locator(right)
    if locators_match(left_mandatory, left_assistive, right_mandatory, right_assistive):
        return True
    left_runtime = runtime_correlation_key(left)
    right_runtime = runtime_correlation_key(right)
    return left_runtime is not None and left_runtime == right_runtime


def _matches_solipsys_capture(identity, capture):
    from automation_harness.core.solipsys_identity import locators_match, locator_is_complete, strategy_parts
    expected_mandatory, expected_assistive = strategy_parts({"identification": identity})
    actual_mandatory, actual_assistive = _solipsys_capture_locator(capture)
    matched = locators_match(
        expected_mandatory, expected_assistive, actual_mandatory, actual_assistive,
    )
    return matched, {
        "mandatory": expected_mandatory,
        "actual_mandatory": actual_mandatory,
        "identity_complete": locator_is_complete(expected_mandatory),
        "scope": expected_assistive,
        "actual_scope": actual_assistive,
        "unsupported_mandatory_keys": [],
    }


def _solipsys_diagnostic_summary(capture):
    mandatory, assistive = _solipsys_capture_locator(capture)
    properties = dict(capture.backend_properties or {})
    return {
        "semantic_identity": {key: mandatory.get(key) for key in (
            "rendered_class", "track_class", "track_identity_key", "track_identity_value",
        )},
        "identity_state": properties.get("identity_state", "candidate"),
        "identity_visible_match_count": properties.get("identity_visible_match_count"),
        "identity_unique_in_visible_scope": properties.get("identity_unique_in_visible_scope"),
        "identity_rejection_reason": properties.get("identity_rejection_reason"),
        "scope": assistive,
        "runtime": {
            "track_runtime_ref": properties.get("track_runtime_ref"),
            "rendered_object_ref": properties.get("rendered_object_ref") or properties.get("ref"),
            "surface_ref": properties.get("surface_ref"),
            "bounds": capture.bounds,
        },
        "track_instance_candidates": properties.get("track_instance_candidates", {}),
    }


def _matches_javafx_capture(identity, capture):
    if not isinstance(identity, Mapping):
        return False, {"reason": "identification_not_mapping"}
    mandatory = identity.get("mandatory", identity)
    assistive = identity.get("assistive", {})
    if not isinstance(mandatory, Mapping) or not mandatory:
        return False, {"reason": "mandatory_not_mapping"}
    if not isinstance(assistive, Mapping):
        return False, {"reason": "assistive_not_mapping"}
    node = _javafx_capture_node(capture)
    mandatory_match = _javafx_node_matches(node, mandatory)
    weak_mandatory = set(mandatory).issubset({"class", "accessible_role"})
    assistive_match = True
    if mandatory_match and weak_mandatory:
        assistive_match = bool(assistive) and _javafx_node_matches(node, assistive)
    expected_ordinal = identity.get("ordinal")
    actual_identity = _javafx_capture_identity(capture) or {}
    ordinal_match = expected_ordinal is None or expected_ordinal == actual_identity.get("ordinal")
    return mandatory_match and assistive_match and ordinal_match, {
        "mandatory": dict(mandatory),
        "mandatory_matched": mandatory_match,
        "weak_mandatory": weak_mandatory,
        "assistive": dict(assistive),
        "assistive_matched": assistive_match,
        "ordinal": expected_ordinal,
        "ordinal_matched": ordinal_match,
    }


def _javafx_capture_node(capture):
    properties = dict(capture.backend_properties or {})
    return {
        "id": capture.accessible_id or properties.get("javafx_id"),
        "class": capture.native_class,
        "accessible_role": properties.get("accessible_role") or capture.role,
        "accessible_text": properties.get("accessible_text"),
        "text": properties.get("text"),
        "window": capture.window or capture.application,
        "hierarchy": list(properties.get("hierarchy") or capture.hierarchy),
        "stable_ancestors": list(properties.get("stable_ancestors") or ()),
        "user_data": properties.get("user_data"),
        "properties": dict(properties.get("node_properties") or {}),
        "layout": dict(properties.get("layout") or {}),
        "style_classes": list(properties.get("style_classes") or ()),
        "sibling_index": properties.get("sibling_index"),
        "sibling_count": properties.get("sibling_count"),
        "parent": {
            "id": capture.parent_accessible_id,
            "accessible_text": capture.parent_name,
            "accessible_role": capture.parent_role,
        },
    }


def _javafx_capture_identity(capture):
    try:
        strategy = capture.candidate_strategy()
    except (TypeError, ValueError):
        return None
    if strategy.type != "javafx" or not isinstance(strategy.options, Mapping):
        return None
    identity = strategy.options.get("identification")
    return dict(identity) if isinstance(identity, Mapping) else None


def _javafx_runtime_ref(capture):
    properties = dict(capture.backend_properties or {})
    ref = properties.get("node_ref")
    pid = properties.get("bridge_pid")
    return (pid, ref) if ref is not None else None


def _adapter_details(adapter):
    return {
        "type": "%s.%s" % (type(adapter).__module__, type(adapter).__name__),
        "repr": repr(adapter),
        "available": getattr(adapter, "available", None),
    }
