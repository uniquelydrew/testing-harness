"""Persistent semantic identity and ephemeral runtime ownership.

Never serialize RuntimeBinding as a repository locator. Process IDs and X11
window handles are only evidence about the currently observed session.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ApplicationIdentity:
    application_id: Optional[str] = None
    executable: Optional[str] = None

    def __post_init__(self):
        if not self.application_id and not self.executable:
            raise ValueError("application identity requires an application ID or executable")


@dataclass(frozen=True)
class WindowIdentity:
    application: ApplicationIdentity
    role: str
    title: Optional[str] = None
    accessible_id: Optional[str] = None

    def __post_init__(self):
        if not self.role or not self.role.strip():
            raise ValueError("window identity requires a semantic role")
        if not self.title and not self.accessible_id:
            raise ValueError("window identity requires a title or accessible ID")


@dataclass(frozen=True)
class ComponentIdentity:
    window: WindowIdentity
    role: str
    name: Optional[str] = None
    accessible_id: Optional[str] = None
    lineage: Tuple[str, ...] = ()

    def __post_init__(self):
        if not self.role:
            raise ValueError("component identity requires a role")
        if not (self.name or self.accessible_id or self.lineage):
            raise ValueError("component identity requires a semantic discriminator")


@dataclass(frozen=True)
class RuntimeBinding:
    process_id: Optional[int] = None
    native_window_id: Optional[int] = None
    backend: Optional[str] = None

    def __post_init__(self):
        for value in (self.process_id, self.native_window_id):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
                raise ValueError("runtime identifiers must be positive integers")


def assess_runtime_ownership(x11_pid, accessibility_pid):
    """Classify live evidence without confusing missing evidence with mismatch."""
    if x11_pid is None or accessibility_pid is None:
        return "unverified"
    return "confirmed" if x11_pid == accessibility_pid else "mismatch"
