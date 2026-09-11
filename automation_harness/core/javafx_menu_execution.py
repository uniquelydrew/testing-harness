"""Execution fallback for logical JavaFX menu paths.

Normal MenuBar/Menu/MenuButton owners execute through the native bridge's
``select_menu_path`` operation. ContextMenu roots are transient PopupControls
and are not part of the ordinary scene-graph node search on all supported
OpenJFX builds. When a logical owner cannot be traversed directly, resolve the
terminal rendered item by its durable logical selector and click its live bounds.
No JavaFX skin class is persisted or consulted as identity.
"""
from __future__ import annotations

from typing import Mapping

from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.models.gui import ActionType


_INSTALLED = False
_ORIGINAL = None


def install_javafx_menu_execution() -> None:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    _ORIGINAL = JavaFxBridgeDriver.select_menu_path

    def select_menu_path(self, selectors, *, identification=None, **kwargs):
        try:
            return _ORIGINAL(
                self,
                selectors,
                identification=identification,
                **kwargs,
            )
        except Exception as owner_error:
            return _select_rendered_terminal(
                self,
                selectors,
                owner_error=owner_error,
            )

    JavaFxBridgeDriver.select_menu_path = select_menu_path
    JavaFxBridgeDriver._logical_menu_execution_installed = True
    _INSTALLED = True


def _select_rendered_terminal(driver, selectors, *, owner_error):
    if not isinstance(selectors, (list, tuple)) or not selectors:
        raise owner_error
    terminal = selectors[-1]
    if not isinstance(terminal, Mapping):
        raise owner_error
    criteria = terminal.get("criteria")
    if not isinstance(criteria, Mapping):
        raise owner_error

    durable = {
        key: value for key, value in criteria.items()
        if key in {"id", "text", "accessible_text", "accessible_role"}
        and value not in (None, "")
    }
    # An ID is preferred because the live ERSA ContextMenu capture supplies the
    # logical MenuItem id on its disposable MenuItemContainer. Text remains a
    # supported fallback for application menus that do not assign IDs.
    if not durable:
        raise owner_error

    endpoint, node, trace = driver._find_unique({"mandatory": durable})
    bounds = node.get("bounds")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise RuntimeError(
            "logical JavaFX menu item resolved but exposes no clickable bounds; "
            "owner traversal failed first: %s: %s"
            % (type(owner_error).__name__, owner_error)
        )

    from automation_harness.core.pointer_actions import click_bounds

    payload = click_bounds(tuple(bounds), ActionType.CLICK)
    return {
        "action": "select_menu_item",
        "fallback": "rendered_terminal",
        "bridge_pid": endpoint.pid,
        "selector": dict(durable),
        "resolution_stages": [stage.to_dict() for stage in trace],
        "pointer": payload,
        "owner_error": "%s: %s" % (type(owner_error).__name__, owner_error),
    }
