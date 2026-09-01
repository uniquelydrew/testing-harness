from __future__ import annotations

"""Bounds-based desktop pointer actions for resolved GUI objects."""

import ctypes
import platform
import time
from typing import Any


def click_bounds(bounds: Any, action: Any = "click") -> dict[str, Any]:
    """Generate a real pointer click at the center of resolved screen bounds."""
    from automation_harness.models.gui import ActionType

    semantic = ActionType(action)
    if semantic not in {ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK}:
        raise ValueError("pointer action must be click, double_click, or right_click")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        raise ValueError("pointer click requires [x, y, width, height] bounds")
    left, top, width, height = (int(round(float(value))) for value in bounds)
    if width <= 0 or height <= 0:
        raise ValueError("pointer click requires positive component bounds")
    x = left + width // 2
    y = top + height // 2

    if platform.system() == "Linux":
        try:
            import pyatspi  # type: ignore
        except ImportError as exc:
            raise RuntimeError("pyatspi is required for Linux pointer injection") from exc
        event = "b3c" if semantic == ActionType.RIGHT_CLICK else "b1c"
        clicks = 2 if semantic == ActionType.DOUBLE_CLICK else 1
        for index in range(clicks):
            pyatspi.Registry.generateMouseEvent(x, y, event)
            if index + 1 < clicks:
                time.sleep(0.08)
    elif platform.system() == "Windows":
        user32 = ctypes.windll.user32
        if not user32.SetCursorPos(x, y):
            raise RuntimeError("Windows pointer could not move to resolved component")
        if semantic == ActionType.RIGHT_CLICK:
            down, up = 0x0008, 0x0010
        else:
            down, up = 0x0002, 0x0004
        clicks = 2 if semantic == ActionType.DOUBLE_CLICK else 1
        for index in range(clicks):
            user32.mouse_event(down, 0, 0, 0, 0)
            user32.mouse_event(up, 0, 0, 0, 0)
            if index + 1 < clicks:
                time.sleep(0.08)
    else:
        raise RuntimeError("desktop pointer injection is supported only on Linux and Windows")

    return {
        "x": x,
        "y": y,
        "button": 3 if semantic == ActionType.RIGHT_CLICK else 1,
        "clicks": 2 if semantic == ActionType.DOUBLE_CLICK else 1,
    }
