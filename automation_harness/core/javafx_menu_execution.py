"""Require owner-scoped native traversal for JavaFX menu actions."""
from __future__ import annotations

from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver


def _select_rendered_terminal(driver, selectors, *, owner_error: BaseException | None = None):
    """Use an already-resolved terminal item's rendered bounds as a safe fallback.

    This intentionally requires the durable terminal selector and never clicks
    a coordinate inferred from an unrelated popup root.
    """
    from automation_harness.core.pointer_actions import click_bounds

    if not selectors or not isinstance(selectors[-1], dict):
        raise RuntimeError("JavaFX menu fallback requires a terminal selector") from owner_error
    criteria = dict(selectors[-1].get("criteria") or {})
    if not criteria:
        raise RuntimeError("JavaFX menu fallback requires terminal criteria") from owner_error
    endpoint, node, _stages = driver._find_unique({"mandatory": criteria})
    bounds = node.get("bounds") if isinstance(node, dict) else None
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise RuntimeError("JavaFX menu fallback requires rendered terminal bounds") from owner_error
    pointer = click_bounds(tuple(bounds), action="click")
    return {
        "fallback": "rendered_terminal",
        "selector": criteria,
        "pointer": pointer,
        "endpoint_pid": getattr(endpoint, "pid", None),
    }


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
