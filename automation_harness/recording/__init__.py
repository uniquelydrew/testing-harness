"""Semantic, authoring-only interaction recording.

Adapters translate native UI events into observations. Repository matching is
read-only; accepted capture/inventory changes are committed later by the
authoring review workflow.
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
