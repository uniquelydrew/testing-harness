"""Deprecated import shim for the pre-live-desktop backend name.

New code must import :class:`LiveDesktopBackend` directly. The compatibility
class intentionally ignores historical constructor metadata; application
identity belongs to object locators, not the execution backend.
"""
from __future__ import annotations

from automation_harness.backends.live_desktop import LiveDesktopBackend


class AttachedDesktopBackend(LiveDesktopBackend):
    """Compatibility alias for :class:`LiveDesktopBackend`."""

    def __init__(self, *_args, **_kwargs) -> None:
        super().__init__()
