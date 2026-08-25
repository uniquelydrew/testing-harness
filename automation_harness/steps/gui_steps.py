"""Capability-driven GUI steps.

Convenience entry points intentionally delegate to ``gui.object.action`` so
the composer and programmatic callers use exactly the same validation path.
"""
from __future__ import annotations

from typing import Any, Mapping

from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext
from automation_harness.models.gui import GuiAction


@step("gui.object.action", domain="gui", description="Execute a semantic action against a logical GUI object.", capabilities={"components"}, risk="application_control", outputs={"execution": "$", "strategy": "strategy", "action": "action"})
def gui_object_action(ctx: TestContext, component_id: str, action: Mapping[str, Any] | str, *, strategy: str | None = None):
    return ctx.component(component_id).execute(GuiAction.from_value(action), strategy=strategy)


@step("gui.object.property.get", domain="gui", description="Read a normalized GUI property.", capabilities={"components"}, outputs={"value": "$"})
def gui_object_property(ctx: TestContext, component_id: str, property_name: str):
    return ctx.component(component_id).property(property_name)


@step("gui.object.state.assert", domain="gui", description="Assert normalized GUI state or a property.", capabilities={"components"}, outputs={"state": "$"})
def gui_object_state_assert(ctx: TestContext, component_id: str, state_name: str, expected: Any):
    return ctx.component(component_id).assert_state(**{state_name: expected})


@step("gui.button.click", domain="gui", description="Convenience alias for a semantic click.", capabilities={"components"}, risk="application_control", outputs={"execution": "$"})
def gui_button_click(ctx: TestContext, component_id: str):
    return gui_object_action(ctx, component_id, "click")


@step("gui.text.set", domain="gui", description="Convenience alias for semantic text entry.", capabilities={"components"}, risk="application_control", outputs={"execution": "$"})
def gui_text_set(ctx: TestContext, component_id: str, value: str):
    return gui_object_action(ctx, component_id, {"type": "set_text", "value": value})


@step("gui.selection.select", domain="gui", description="Convenience alias for semantic item selection.", capabilities={"components"}, risk="application_control", outputs={"execution": "$"})
def gui_selection_select(ctx: TestContext, component_id: str, selector: Mapping[str, Any]):
    return gui_object_action(ctx, component_id, {"type": "select_item", "selector": selector})
