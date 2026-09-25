"""Require owner-scoped native traversal for JavaFX menu actions."""
from __future__ import annotations

from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver


_INSTALLED = False


def install_javafx_menu_execution() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    original = JavaFxBridgeDriver.select_menu_path

    def select_menu_path(self, selectors, *, identification=None, **kwargs):
        try:
            return original(self, selectors, identification=identification, **kwargs)
        except Exception as owner_error:
            raise RuntimeError(
                "JavaFX menu owner/path traversal failed; refusing an unscoped terminal click: "
                "%s: %s" % (type(owner_error).__name__, owner_error)
            ) from owner_error

    JavaFxBridgeDriver.select_menu_path = select_menu_path
    _INSTALLED = True
