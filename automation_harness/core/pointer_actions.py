from __future__ import annotations

"""Bounds-based desktop pointer actions for resolved GUI objects."""

import ctypes
import platform
import time
from typing import Any

_INSTALLED = False


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


def install() -> None:
    """Make Click a pointer operation instead of an alias for Activate."""
    global _INSTALLED
    if _INSTALLED:
        return

    from automation_harness.core.component_handle import (
        ComponentHandle,
        ComponentResolutionError,
        UnsupportedGuiAction,
    )
    from automation_harness.models.component import ComponentDefinition
    from automation_harness.models.gui import (
        ActionType,
        ExecutionResult,
        GuiAction,
        PASSIVE_POINTER_TYPES,
        default_actions,
    )

    original_execute = ComponentHandle.execute

    def execute(self, action, *, strategy=None):
        semantic = GuiAction.from_value(action)
        if semantic.type not in {ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK}:
            return original_execute(self, action, strategy=strategy)
        if not self.supports(semantic.type):
            available = ", ".join(sorted(item.value for item in self.definition.semantic_actions)) or "none"
            raise UnsupportedGuiAction(
                "object %r type %s does not support %s; supported actions: %s"
                % (
                    self.definition.component_id,
                    self.definition.object_type.value,
                    semantic.type.value,
                    available,
                )
            )

        resolved = self.resolve()
        bounds = resolved.metadata.get("bounds")
        if bounds is None:
            raise ComponentResolutionError(
                "resolved object %r has no screen bounds for pointer interaction"
                % self.definition.component_id
            )
        try:
            payload = click_bounds(bounds, semantic.type)
        except Exception as exc:
            self.context.evidence.record(
                "gui_action_attempt_failed",
                component_id=self.definition.component_id,
                action=semantic.to_dict(),
                strategy="pointer",
                error="%s: %s" % (type(exc).__name__, exc),
            )
            raise
        payload["resolved_strategy"] = resolved.strategy
        result = ExecutionResult(
            semantic.type,
            "pointer",
            payload,
            ({"strategy": "pointer", "success": True},),
        )
        self.context.evidence.record(
            "gui_action_executed",
            component_id=self.definition.component_id,
            action=semantic.to_dict(),
            strategy="pointer",
            result=dict(payload),
        )
        return result

    def semantic_actions(self):
        values = set()
        for action in self.actions:
            if action == "activate":
                values.update({ActionType.ACTIVATE, ActionType.CLICK})
                continue
            try:
                values.add(ActionType(action))
            except ValueError:
                continue
        if (
            any(strategy.type != "reference_inspection" for strategy in self.strategies)
            and self.object_type not in PASSIVE_POINTER_TYPES
        ):
            values.add(ActionType.CLICK)
        return frozenset(values) or default_actions(self.object_type)

    ComponentHandle.execute = execute
    ComponentDefinition.semantic_actions = property(semantic_actions)
    _INSTALLED = True
