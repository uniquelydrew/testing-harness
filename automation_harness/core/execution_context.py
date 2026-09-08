"""Execution-scoped object lineage without mutating repository definitions."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import threading
from typing import Any, Iterator, Mapping

from automation_harness.models.component import ComponentDefinition, ComponentStrategy


class ExecutionContextError(ValueError):
    pass


class ExecutionContextStack:
    def __init__(self) -> None:
        self._windows: list[str] = []
        self._invocation_scopes: list[Mapping[str, Any]] = []

    @property
    def active_window(self) -> str | None:
        for scope in reversed(self._invocation_scopes):
            window = scope.get("window")
            if isinstance(window, str) and window:
                return window
        return self._windows[-1] if self._windows else None

    def push_window(self, component_id: str) -> None:
        if not isinstance(component_id, str) or not component_id:
            raise ExecutionContextError("window context requires a component id")
        self._windows.append(component_id)

    def pop_window(self, expected: str | None = None) -> str:
        if not self._windows:
            raise ExecutionContextError("window context stack is empty")
        current = self._windows[-1]
        if expected is not None and current != expected:
            raise ExecutionContextError(
                f"active window is {current!r}, not expected window {expected!r}"
            )
        return self._windows.pop()

    def apply_effect(self, effect: Mapping[str, Any]) -> str | None:
        """Apply a post-completion context transition.

        Effects are execution state, never repository mutations.  They are
        deliberately applied only after the action completion predicate has
        succeeded, so a dialog is not made the active parent prematurely.
        """
        operation = effect.get("operation")
        if operation == "push":
            component = effect.get("object", effect.get("component"))
            if not isinstance(component, str) or not component:
                raise ExecutionContextError("context push requires a non-empty object")
            self.push_window(component)
            return component
        if operation == "pop":
            expected = effect.get("expected")
            if expected is not None and not isinstance(expected, str):
                raise ExecutionContextError("context pop expected must be a component id")
            return self.pop_window(expected)
        raise ExecutionContextError("context operation must be 'push' or 'pop'")

    @contextmanager
    def scope(self, value: Mapping[str, Any] | None) -> Iterator[None]:
        self._invocation_scopes.append(dict(value or {}))
        try:
            yield
        finally:
            self._invocation_scopes.pop()

    def effective_scope(self, definition: ComponentDefinition) -> Mapping[str, Any]:
        merged = dict(definition.scope)
        for scope in self._invocation_scopes:
            merged.update(scope)
        return merged


class ExecutionSignals:
    """Generation-counted application/test-hook events safe against stale signals."""

    def __init__(self) -> None:
        self._generations: dict[str, int] = {}
        self._lock = threading.RLock()

    def signal(self, name: str) -> int:
        if not isinstance(name, str) or not name:
            raise ExecutionContextError("execution signal requires a non-empty name")
        with self._lock:
            generation = self._generations.get(name, 0) + 1
            self._generations[name] = generation
            return generation

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._generations)

    def occurred_since(self, name: str, baseline: Mapping[str, int]) -> bool:
        with self._lock:
            return self._generations.get(name, 0) > baseline.get(name, 0)


def bind_component_lineage(
    definition: ComponentDefinition,
    scope: Mapping[str, Any],
    components,
    active_window: str | None,
) -> ComponentDefinition:
    parent = scope.get("parent")
    if isinstance(parent, Mapping) and parent.get("from") == "execution.active_window":
        parent = active_window
    elif parent is None and scope.get("from") == "execution.active_window":
        parent = active_window
    if isinstance(parent, Mapping) and isinstance(parent.get("component"), str):
        parent = parent["component"]
    if parent is None:
        return definition

    parent_identity: Mapping[str, Any]
    if isinstance(parent, str):
        if not components.contains(parent):
            raise ExecutionContextError(
                f"component {definition.component_id!r} lineage references unknown parent {parent!r}"
            )
        parent_identity = _atspi_identity(components.get(parent))
    elif isinstance(parent, Mapping):
        parent_identity = parent
    else:
        raise ExecutionContextError("component parent scope must be an object id or identity mapping")

    allowed = {key: value for key, value in parent_identity.items() if key in {"name", "role", "accessible_id"}}
    if not allowed:
        raise ExecutionContextError(
            f"component {definition.component_id!r} parent exposes no usable lineage identity"
        )
    strategies: list[ComponentStrategy] = []
    for strategy in definition.strategies:
        if strategy.type not in {"atspi", "java_accessibility"}:
            strategies.append(strategy)
            continue
        options = dict(strategy.options)
        identification = dict(options.get("identification", {}))
        mandatory = dict(identification.get("mandatory", {}))
        assistive = dict(identification.get("assistive", {}))
        existing = assistive.get("parent")
        if existing is not None and dict(existing) != allowed:
            raise ExecutionContextError(
                f"component {definition.component_id!r} invocation lineage conflicts with repository parent"
            )
        assistive["parent"] = allowed
        identification["mandatory"] = mandatory
        identification["assistive"] = assistive
        options["identification"] = identification
        strategies.append(ComponentStrategy(strategy.type, options))
    return replace(definition, strategies=tuple(strategies))


def _atspi_identity(definition: ComponentDefinition) -> Mapping[str, Any]:
    for strategy in definition.strategies:
        if strategy.type not in {"atspi", "java_accessibility"}:
            continue
        identification = strategy.options.get("identification", {})
        mandatory = identification.get("mandatory", {}) if isinstance(identification, Mapping) else {}
        assistive = identification.get("assistive", {}) if isinstance(identification, Mapping) else {}
        result = {
            key: value
            for key, value in {**dict(assistive), **dict(mandatory)}.items()
            if key in {"name", "role", "accessible_id"}
        }
        if result:
            return result
    raise ExecutionContextError(f"parent component {definition.component_id!r} has no AT-SPI identity")
