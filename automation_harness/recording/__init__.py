"""Semantic, authoring-only interaction recording.

Adapters translate native UI events into observations. JavaFX menu recording
normalizes transient popup skin events into logical menu owners/subobjects before
repository matching so a MenuItem is never authored as a standalone skin node.
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
from automation_harness.core.logical_menu import (
    ensure_recorded_menu_owner,
    is_javafx_menu_skin_capture,
)


# Install after session.py is fully imported to avoid a circular dependency.
# The repository object is shared with authoring, so a newly discovered logical
# ContextMenu owner becomes visible to the workbench/save flow immediately.
_original_recording_match = RecordingSession._match


def _logical_menu_recording_match(self, target):
    if (
        target is not None
        and self.repository is not None
        and is_javafx_menu_skin_capture(target)
    ):
        ensure_recorded_menu_owner(self.repository, target)
    return _original_recording_match(self, target)


if not getattr(RecordingSession, "_logical_menu_owner_installed", False):
    RecordingSession._match = _logical_menu_recording_match
    RecordingSession._logical_menu_owner_installed = True


__all__ = [
    "ActionFired", "FocusChanged", "KeyboardInput", "Observation",
    "PointerInteraction", "StateChanged", "TextChanged", "WindowChanged",
    "RecordedInteraction", "RecordingSession", "RepositoryMatch", "StateDelta",
    "interactions_to_steps", "EvidencePolicy", "policy_for",
]
