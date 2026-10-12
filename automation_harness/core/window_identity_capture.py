"""Identify top-level windows using durable accessibility properties.

The returned identity intentionally excludes runtime process IDs and X11 handles.
"""
from automation_harness.core.object_identity import ApplicationIdentity, WindowIdentity
from automation_harness.models.gui import ObjectType


def identify_captured_window(captured):
    if captured.semantic_type() not in (ObjectType.WINDOW, ObjectType.DIALOG):
        raise ValueError("capture does not describe a window or dialog")
    application = (captured.application or "").strip()
    if not application:
        raise ValueError("window application identity is unavailable")
    title = (captured.name or captured.window or "").strip() or None
    accessible_id = (captured.accessible_id or "").strip() or None
    if title is None and accessible_id is None:
        raise ValueError("window has no durable title or accessible ID")
    return WindowIdentity(
        application=ApplicationIdentity(application_id=application),
        role=captured.role or "window",
        title=title,
        accessible_id=accessible_id,
    )


def validated_window_screen_region(captured, unobstructed):
    """Only permit screen crops when target contents are unobstructed."""
    identify_captured_window(captured)
    if not unobstructed:
        raise ValueError("window is obscured; screen crop cannot prove window contents")
    bounds = captured.bounds
    if bounds is None or len(bounds) != 4 or bounds[2] <= 0 or bounds[3] <= 0:
        raise ValueError("window has invalid screenshot bounds")
    return tuple(int(value) for value in bounds)
