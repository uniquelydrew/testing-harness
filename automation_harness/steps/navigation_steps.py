from __future__ import annotations

from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext
from automation_harness.models.component import ResolvedComponent


@step(
    "navigation.component.resolve",
    domain="navigation",
    description="Resolve a logical component using its registered locator strategy chain.",
    capabilities={"components"},
    aliases=("resolve_component",),
    outputs={"component": "$", "component_id": "component_id", "strategy": "strategy"},
)
def resolve_component(ctx: TestContext, component_id: str) -> ResolvedComponent:
    return ctx.component(component_id).resolve()


@step(
    "navigation.component.activate",
    domain="navigation",
    description="Resolve and activate a logical component without exposing its implementation strategy.",
    capabilities={"components"},
    risk="application_control",
    aliases=("activate_component",),
    outputs={"result": "$", "action": "action"},
)
def activate_component(ctx: TestContext, component_id: str) -> dict:
    return ctx.component(component_id).activate()


@step(
    "component.state.get",
    domain="component",
    description="Observe the current backend-neutral state of one logical component.",
    capabilities={"components"},
    outputs={"state": "$", "present": "present", "visible": "visible", "enabled": "enabled", "expanded": "expanded"},
)
def get_component_state(ctx: TestContext, component_id: str):
    return ctx.component(component_id).state()


@step(
    "component.property.get",
    domain="component",
    description="Read one first-class state or backend property from a logical component.",
    capabilities={"components"},
    outputs={"value": "$"},
)
def get_component_property(ctx: TestContext, component_id: str, property_name: str):
    return ctx.component(component_id).property(property_name)


@step(
    "component.state.wait",
    domain="component",
    description="Wait until one component state/property equals the expected value.",
    capabilities={"components"},
    outputs={"state": "$"},
)
def wait_component_state(
    ctx: TestContext,
    component_id: str,
    state_name: str,
    expected,
    *,
    timeout: float = 5.0,
    interval: float = 0.1,
):
    return ctx.component(component_id).wait_for(timeout=timeout, interval=interval, **{state_name: expected})


@step(
    "component.state.assert",
    domain="component",
    description="Assert that one component state/property equals the expected value.",
    capabilities={"components"},
    outputs={"state": "$"},
)
def assert_component_state(ctx: TestContext, component_id: str, state_name: str, expected):
    return ctx.component(component_id).assert_state(**{state_name: expected})


@step(
    "component.assertion.evaluate",
    domain="component",
    description="Evaluate a reusable assertion declared by an object repository entry.",
    capabilities={"components"},
    outputs={"state": "$"},
)
def evaluate_component_assertion(ctx: TestContext, component_id: str, assertion_id: str):
    return ctx.component(component_id).assert_named(assertion_id)


@step("component.text.get", domain="component", capabilities={"text-input"}, outputs={"text": "$"})
def get_component_text(ctx: TestContext, component_id: str) -> str:
    return ctx.component(component_id).get_text()


@step("component.text.set", domain="component", capabilities={"text-input"}, risk="application_control", outputs={"result": "$"})
def set_component_text(ctx: TestContext, component_id: str, value: str) -> dict:
    return ctx.component(component_id).set_text(value)


@step("component.selection.get", domain="component", capabilities={"selection"}, outputs={"selected": "$"})
def get_component_selection(ctx: TestContext, component_id: str) -> list[str]:
    return ctx.component(component_id).get_selection()


@step("component.selection.select", domain="component", capabilities={"selection"}, risk="application_control", outputs={"result": "$"})
def select_component_child(ctx: TestContext, component_id: str, child_index: int) -> dict:
    return ctx.component(component_id).select_child(child_index)


@step("component.value.get", domain="component", capabilities={"value-control"}, outputs={"value": "$"})
def get_component_value(ctx: TestContext, component_id: str) -> float:
    return ctx.component(component_id).get_value()


@step("component.value.set", domain="component", capabilities={"value-control"}, risk="application_control", outputs={"result": "$"})
def set_component_value(ctx: TestContext, component_id: str, value: float) -> dict:
    return ctx.component(component_id).set_value(value)
