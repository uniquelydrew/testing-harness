"""Semantic, authoring-only interaction recording.

Adapters translate native UI events into observations; this package deliberately
does not persist raw framework telemetry or mutate an object repository.
"""

from automation_harness.recording.observations import (
    ActionFired,
    FocusChanged,
    KeyboardInput,
    Observation,
    PointerInteraction,
    StateChanged,
    TextChanged,
    WindowChanged,
)
from automation_harness.recording.session import (
    RecordedInteraction,
    RecordingSession,
    RepositoryMatch,
    StateDelta,
    interactions_to_steps,
)
from automation_harness.recording.evidence import EvidencePolicy, policy_for

__all__ = [
    "ActionFired", "FocusChanged", "KeyboardInput", "Observation",
    "PointerInteraction", "StateChanged", "TextChanged", "WindowChanged",
    "RecordedInteraction", "RecordingSession", "RepositoryMatch", "StateDelta",
    "interactions_to_steps", "EvidencePolicy", "policy_for",
]
