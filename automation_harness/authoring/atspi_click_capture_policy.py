"""Process-scoped hybrid capture policy for a desktop pointer click.

AT-SPI is useful for locating the X11 owner, but a native Java/JavaFX capture
is preferred once that process is known.  Keeping this policy outside either
driver prevents a transient popup from being attributed to a covered widget.
"""
from __future__ import annotations

from typing import Any


def _owner_pid_at_current_pointer(_x: int, _y: int) -> int | None:
    """Best-effort hook for the X11 owner lookup; unavailable environments fall back."""
    return None


def _resolve_hybrid_point(service: Any, coordinates: tuple[int, int], *, scoped: bool = True):
    x, y = coordinates
    process_id = _owner_pid_at_current_pointer(x, y) if scoped else None
    java_agent = getattr(service, "java_agent_driver", None)
    javafx = getattr(service, "javafx_driver", None)
    atspi = getattr(service, "driver", None)

    for native in (java_agent, javafx):
        if not getattr(native, "available", False) or process_id is None:
            continue
        try:
            return native.capture_at_point(x, y, process_id=process_id)
        except (LookupError, RuntimeError):
            pass

    captured = None
    if getattr(atspi, "available", False):
        snapshot = getattr(atspi, "capture_at_point_snapshot", None)
        captured = snapshot(x, y, excluded_application_prefixes=()) if callable(snapshot) else atspi.capture_at_point(x, y)
    if captured is None:
        raise LookupError("no capture backend resolved the pointer target")

    inferred_pid = dict(getattr(captured, "backend_properties", {}) or {}).get("process_id")
    if inferred_pid is not None:
        for native in (java_agent, javafx):
            if not getattr(native, "available", False):
                continue
            try:
                return native.capture_at_point(x, y, process_id=int(inferred_pid))
            except (LookupError, RuntimeError, ValueError):
                pass
    return captured
