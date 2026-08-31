"""Small, framework-neutral inputs to the recording correlator."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from automation_harness.models.component import CapturedComponent


@dataclass(frozen=True)
class Observation:
    timestamp: float
    source: str
    target: CapturedComponent | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PointerInteraction(Observation):
    button: str = "primary"
    phase: str = "released"
    coordinates: tuple[int, int] | None = None


@dataclass(frozen=True)
class KeyboardInput(Observation):
    key: str = ""
    phase: str = "pressed"
    modifiers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ActionFired(Observation):
    action: str = "activate"


@dataclass(frozen=True)
class TextChanged(Observation):
    before: str | None = None
    after: str | None = None


@dataclass(frozen=True)
class StateChanged(Observation):
    property: str = ""
    before: Any = None
    after: Any = None


@dataclass(frozen=True)
class FocusChanged(Observation):
    focused: bool = True


@dataclass(frozen=True)
class WindowChanged(Observation):
    window: str | None = None
