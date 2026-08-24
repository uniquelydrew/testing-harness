from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from typing import Any, Mapping

from automation_harness.utils.evidence import EvidenceRecorder


class VariableError(ValueError):
    """Base error for test-global variable operations."""


class VariableNotFoundError(VariableError):
    """Raised when a variable reference cannot be resolved."""


class VariableTypeError(VariableError):
    """Raised when append/update is incompatible with the stored value."""


@dataclass(frozen=True)
class VariableRef:
    """Deferred reference to a test-global variable or nested value.

    Paths use dot notation. For example, ``ctx.ref("track.track_id")`` resolves
    ``track`` from the test-global store and then reads the ``track_id`` mapping
    key or object attribute. Integer list indices are also supported.
    """

    path: str

    def __post_init__(self) -> None:
        _validate_path(self.path)

    def __str__(self) -> str:
        return f"${{{self.path}}}"


class VariableStore:
    """Mutable, test-scoped global variable store shared by every registered step.

    A fresh store is created for every pytest test context. Bundle/CLI initial
    values are deep-copied into the store, preventing one test from leaking state
    into another while still providing UFT-like global data within a test flow.
    """

    def __init__(
        self,
        evidence: EvidenceRecorder,
        initial: Mapping[str, Any] | None = None,
    ) -> None:
        self._evidence = evidence
        self._values: dict[str, Any] = {}
        self._lock = threading.RLock()
        if initial:
            self.initialize(initial)

    def initialize(self, values: Mapping[str, Any], *, overwrite: bool = False) -> None:
        if not isinstance(values, Mapping):
            raise VariableTypeError("global variable initialization requires a mapping")
        with self._lock:
            for name, value in values.items():
                _validate_name(name)
                if name in self._values and not overwrite:
                    raise VariableError(f"global variable {name!r} is already initialized")
                resolved = self.resolve_value(value)
                self._values[name] = copy.deepcopy(resolved)
                self._evidence.record(
                    "variable_initialized",
                    variable=name,
                    value=self._values[name],
                    overwrite=overwrite,
                )

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        return self.contains(name)

    def __getitem__(self, path: str) -> Any:
        return self.get(path)

    def __setitem__(self, name: str, value: Any) -> None:
        self.set(name, value)

    def contains(self, name: str) -> bool:
        _validate_name(name)
        with self._lock:
            return name in self._values

    def ref(self, path: str) -> VariableRef:
        return VariableRef(path)

    def get(self, path: str, default: Any = ...,) -> Any:
        _validate_path(path)
        with self._lock:
            try:
                value = _resolve_path(self._values, path)
            except VariableNotFoundError:
                if default is not ...:
                    return default
                raise
            result = copy.deepcopy(value)
        self._evidence.record("variable_read", variable=path, value=result)
        return result

    def set(self, name: str, value: Any) -> Any:
        _validate_name(name)
        resolved = self.resolve_value(value)
        with self._lock:
            previous = copy.deepcopy(self._values.get(name)) if name in self._values else None
            self._values[name] = copy.deepcopy(resolved)
            result = copy.deepcopy(self._values[name])
        self._evidence.record(
            "variable_set",
            variable=name,
            previous=previous,
            value=result,
        )
        return result

    def set_many_atomic(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Validate and commit multiple top-level values as one transaction.

        No value is visible if validation/resolution of any member fails. Evidence
        is emitted only after the complete mapping has committed.
        """
        if not isinstance(values, Mapping):
            raise VariableTypeError("atomic global variable set requires a mapping")
        resolved: dict[str, Any] = {}
        for name, value in values.items():
            _validate_name(name)
            resolved[name] = self.resolve_value(value)
        with self._lock:
            previous = {
                name: copy.deepcopy(self._values.get(name)) if name in self._values else None
                for name in resolved
            }
            staged = copy.deepcopy(self._values)
            for name, value in resolved.items():
                staged[name] = copy.deepcopy(value)
            self._values = staged
            committed = {name: copy.deepcopy(self._values[name]) for name in resolved}
        for name, value in committed.items():
            self._evidence.record(
                "variable_set",
                variable=name,
                previous=previous[name],
                value=value,
                transaction=True,
            )
        self._evidence.record(
            "variable_transaction_committed",
            variables=sorted(committed),
        )
        return committed

    def update(self, name: str, values: Mapping[str, Any]) -> dict[str, Any]:
        _validate_name(name)
        if not isinstance(values, Mapping):
            raise VariableTypeError("global variable update requires a mapping")
        resolved = self.resolve_value(dict(values))
        with self._lock:
            if name not in self._values:
                current: dict[str, Any] = {}
                self._values[name] = current
            else:
                existing = self._values[name]
                if not isinstance(existing, dict):
                    raise VariableTypeError(
                        f"global variable {name!r} is {type(existing).__name__}, not a mapping"
                    )
                current = existing
            previous = copy.deepcopy(current)
            current.update(copy.deepcopy(resolved))
            result = copy.deepcopy(current)
        self._evidence.record(
            "variable_updated",
            variable=name,
            previous=previous,
            updates=resolved,
            value=result,
        )
        return result

    def append(self, name: str, value: Any) -> list[Any]:
        _validate_name(name)
        resolved = self.resolve_value(value)
        with self._lock:
            if name not in self._values:
                current: list[Any] = []
                self._values[name] = current
            else:
                existing = self._values[name]
                if not isinstance(existing, list):
                    raise VariableTypeError(
                        f"global variable {name!r} is {type(existing).__name__}, not a list"
                    )
                current = existing
            current.append(copy.deepcopy(resolved))
            result = copy.deepcopy(current)
        self._evidence.record(
            "variable_appended",
            variable=name,
            appended=resolved,
            value=result,
        )
        return result

    def extend(self, name: str, values: list[Any] | tuple[Any, ...]) -> list[Any]:
        _validate_name(name)
        if not isinstance(values, (list, tuple)):
            raise VariableTypeError("global variable extend requires a list or tuple")
        resolved = self.resolve_value(list(values))
        with self._lock:
            if name not in self._values:
                current: list[Any] = []
                self._values[name] = current
            else:
                existing = self._values[name]
                if not isinstance(existing, list):
                    raise VariableTypeError(
                        f"global variable {name!r} is {type(existing).__name__}, not a list"
                    )
                current = existing
            current.extend(copy.deepcopy(resolved))
            result = copy.deepcopy(current)
        self._evidence.record(
            "variable_extended",
            variable=name,
            extended=resolved,
            value=result,
        )
        return result

    def resolve(self, reference: VariableRef) -> Any:
        value = self.get(reference.path)
        self._evidence.record(
            "variable_resolved",
            variable=reference.path,
            value=value,
        )
        return value

    def resolve_value(self, value: Any) -> Any:
        """Recursively resolve VariableRef objects embedded in containers."""
        if isinstance(value, VariableRef):
            return self.resolve(value)
        if isinstance(value, list):
            return [self.resolve_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.resolve_value(item) for item in value)
        if isinstance(value, dict):
            return {key: self.resolve_value(item) for key, item in value.items()}
        return value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._values)


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name.strip() or name != name.strip():
        raise VariableError("global variable names must be non-empty strings without surrounding whitespace")
    if "." in name:
        raise VariableError("top-level global variable names cannot contain '.'; use dots only when referencing nested values")


def _validate_path(path: str) -> None:
    if not isinstance(path, str) or not path.strip() or path != path.strip():
        raise VariableError("global variable references must be non-empty strings without surrounding whitespace")
    if any(not segment for segment in path.split(".")):
        raise VariableError(f"invalid global variable reference {path!r}")


def _resolve_path(values: Mapping[str, Any], path: str) -> Any:
    segments = path.split(".")
    current: Any = values
    traversed: list[str] = []
    for segment in segments:
        traversed.append(segment)
        if isinstance(current, Mapping):
            if segment not in current:
                raise VariableNotFoundError(f"global variable path {path!r} does not exist at {'.'.join(traversed)!r}")
            current = current[segment]
            continue
        if isinstance(current, (list, tuple)):
            try:
                index = int(segment)
            except ValueError as exc:
                raise VariableNotFoundError(
                    f"global variable path {path!r} requires an integer index at {'.'.join(traversed)!r}"
                ) from exc
            try:
                current = current[index]
            except IndexError as exc:
                raise VariableNotFoundError(
                    f"global variable path {path!r} index {index} is out of range"
                ) from exc
            continue
        if hasattr(current, segment):
            current = getattr(current, segment)
            continue
        raise VariableNotFoundError(
            f"global variable path {path!r} cannot traverse {segment!r} on {type(current).__name__}"
        )
    return current
