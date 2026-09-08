"""Capability-driven GUI steps.

Convenience entry points intentionally delegate to ``gui.object.action`` so
the composer and programmatic callers use exactly the same validation path.
"""
from __future__ import annotations

from typing import Any, Mapping

from automation_harness.core.step_registry import step
from automation_harness.core.test_context import TestContext
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.drivers.vision_driver import VisionDriver
from automation_harness.models.evidence import EvidenceItem
from automation_harness.models.gui import GuiAction


def _node_id(ctx: TestContext) -> str | None:
    return getattr(ctx, "execution_node_id", None)


def _activate_owning_window(ctx: TestContext, handle):
    """Raise the object's owning window without focusing or invoking the object."""
    errors = []
    attempted = False
    for strategy in handle.definition.strategies:
        if strategy.type not in {"javafx", "atspi", "java_accessibility"}:
            continue
        attempted = True
        options = dict(strategy.options)
        identification = options.get("identification")
        try:
            if strategy.type == "javafx":
                details = JavaFxBridgeDriver(ctx).activate_window(
                    identification=identification,
                )
            else:
                details = AtspiDriver(ctx).activate_window(
                    identification=identification,
                    name=options.get("name"),
                    role=options.get("role"),
                    accessible_id=options.get("accessible_id"),
                )
            result = {"strategy": strategy.type, "details": details}
            ctx.evidence.record(
                "component_window_activated",
                component_id=handle.definition.component_id,
                strategy=strategy.type,
                details=details,
            )
            return result
        except Exception as exc:
            error = "%s: %s: %s" % (strategy.type, type(exc).__name__, exc)
            errors.append(error)
            ctx.evidence.record(
                "component_window_activation_attempt_failed",
                component_id=handle.definition.component_id,
                strategy=strategy.type,
                error="%s: %s" % (type(exc).__name__, exc),
            )
    if not attempted:
        raise RuntimeError("component has no strategy capable of owning-window activation")
    raise RuntimeError("unable to activate owning window; " + "; ".join(errors))


def _record_component_assertion(
    ctx: TestContext,
    assertion: str,
    component_id: str,
    *,
    passed: bool,
    expected: Any,
    actual: Any,
    evidence,
    message: str | None = None,
    **fields: Any,
) -> None:
    ctx.evidence.record_assertion(
        assertion,
        passed=passed,
        expected=expected,
        actual=actual,
        evidence=evidence,
        node_id=_node_id(ctx),
        component_id=component_id,
        message=message,
        **fields,
    )


@step("gui.object.action", domain="gui", description="Execute a semantic action against a logical GUI object.", capabilities={"components"}, risk="application_control", outputs={"execution": "$", "strategy": "strategy", "action": "action"})
def gui_object_action(ctx: TestContext, component_id: str, action: Mapping[str, Any] | str, *, strategy: str | None = None):
    return ctx.component(component_id).execute(GuiAction.from_value(action), strategy=strategy)


@step("gui.object.property.get", domain="gui", description="Read a normalized GUI property.", capabilities={"components"}, outputs={"value": "$"})
def gui_object_property(ctx: TestContext, component_id: str, property_name: str):
    return ctx.component(component_id).property(property_name)


@step(
    "gui.object.exists.assert",
    domain="gui",
    description="Assert that a logical GUI object resolves and retain a component-bounds screenshot as evidence.",
    capabilities={"components", "screen-capture"},
    outputs={"assertion": "$", "screenshot": "screenshot"},
)
def gui_object_exists_assert(ctx: TestContext, component_id: str):
    expected_item = EvidenceItem.value_item(
        "expected",
        "component_state",
        {"present": True},
        description="Expected component presence",
    )
    handle = ctx.component(component_id)
    try:
        resolved = handle.resolve()
    except Exception as exc:
        actual_item = EvidenceItem.value_item(
            "actual",
            "component_state",
            {"present": False},
            description="Component could not be resolved",
        )
        diagnostic = EvidenceItem.value_item(
            "diagnostic",
            "exception",
            {"type": type(exc).__name__, "message": str(exc)},
            description="Resolution failure",
        )
        message = "component %r did not resolve: %s: %s" % (
            component_id,
            type(exc).__name__,
            exc,
        )
        _record_component_assertion(
            ctx,
            "component_exists",
            component_id,
            passed=False,
            expected=True,
            actual=False,
            evidence=(expected_item, actual_item, diagnostic),
            message=message,
        )
        raise AssertionError(message) from exc

    try:
        window_activation = _activate_owning_window(ctx, handle)
    except Exception as exc:
        actual_item = EvidenceItem.value_item(
            "actual",
            "component_state",
            {
                "present": True,
                "strategy": resolved.strategy,
                "bounds": resolved.metadata.get("bounds"),
            },
            description="Component resolved before evidence capture",
        )
        diagnostic = EvidenceItem.value_item(
            "diagnostic",
            "exception",
            {"type": type(exc).__name__, "message": str(exc)},
            description="Owning window could not be brought to the foreground",
        )
        message = (
            "component %r exists but its owning window could not be activated "
            "before screenshot evidence capture: %s: %s"
            % (component_id, type(exc).__name__, exc)
        )
        _record_component_assertion(
            ctx,
            "component_exists",
            component_id,
            passed=False,
            expected=True,
            actual=True,
            evidence=(expected_item, actual_item, diagnostic),
            message=message,
            strategy=resolved.strategy,
            bounds=resolved.metadata.get("bounds"),
        )
        raise AssertionError(message) from exc

    try:
        resolved = handle.resolve()
    except Exception as exc:
        actual_item = EvidenceItem.value_item(
            "actual",
            "component_state",
            {"present": True, "window_activation": window_activation},
            description="Component resolved before foreground activation but could not be refreshed afterward",
        )
        diagnostic = EvidenceItem.value_item(
            "diagnostic",
            "exception",
            {"type": type(exc).__name__, "message": str(exc)},
            description="Post-activation component refresh failed",
        )
        message = "component %r could not be re-resolved after activating its owning window: %s: %s" % (
            component_id,
            type(exc).__name__,
            exc,
        )
        _record_component_assertion(
            ctx,
            "component_exists",
            component_id,
            passed=False,
            expected=True,
            actual=True,
            evidence=(expected_item, actual_item, diagnostic),
            message=message,
            window_activation=window_activation,
        )
        raise AssertionError(message) from exc

    bounds = resolved.metadata.get("bounds")
    try:
        if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
            raise ValueError("resolved component has no four-value desktop bounds")
        capture_bounds = tuple(int(value) for value in bounds)
        if capture_bounds[2] <= 0 or capture_bounds[3] <= 0:
            raise ValueError("resolved component bounds must have positive width and height")
    except (TypeError, ValueError) as exc:
        actual_item = EvidenceItem.value_item(
            "actual",
            "component_state",
            {
                "present": True,
                "strategy": resolved.strategy,
                "bounds": bounds,
                "window_activation": window_activation,
            },
            description="Component resolved but did not expose usable screenshot bounds",
        )
        diagnostic = EvidenceItem.value_item(
            "diagnostic",
            "exception",
            {"type": type(exc).__name__, "message": str(exc)},
            description="Required evidence capture could not start",
        )
        message = "component %r exists but required screenshot evidence cannot be captured: %s" % (
            component_id,
            exc,
        )
        _record_component_assertion(
            ctx,
            "component_exists",
            component_id,
            passed=False,
            expected=True,
            actual=True,
            evidence=(expected_item, actual_item, diagnostic),
            message=message,
            strategy=resolved.strategy,
            bounds=bounds,
            window_activation=window_activation,
        )
        raise AssertionError(message) from exc

    actual_state = EvidenceItem.value_item(
        "actual",
        "component_state",
        {
            "present": True,
            "strategy": resolved.strategy,
            "bounds": list(capture_bounds),
            "window_activation": window_activation,
        },
        description="Resolved component state",
    )
    try:
        screenshot_path = VisionDriver(ctx).capture_region(
            capture_bounds,
            name="%s-exists" % component_id,
        )
    except Exception as exc:
        diagnostic = EvidenceItem.value_item(
            "diagnostic",
            "exception",
            {"type": type(exc).__name__, "message": str(exc)},
            description="Required screenshot capture failed",
        )
        message = "component %r exists but required screenshot evidence failed: %s: %s" % (
            component_id,
            type(exc).__name__,
            exc,
        )
        _record_component_assertion(
            ctx,
            "component_exists",
            component_id,
            passed=False,
            expected=True,
            actual=True,
            evidence=(expected_item, actual_state, diagnostic),
            message=message,
            strategy=resolved.strategy,
            bounds=list(capture_bounds),
            window_activation=window_activation,
        )
        raise AssertionError(message) from exc

    relative_screenshot = screenshot_path.relative_to(ctx.run_dir).as_posix()
    actual_image = EvidenceItem.artifact(
        "actual",
        "image",
        relative_screenshot,
        description="Observed component",
        metadata={"bounds": list(capture_bounds)},
    )
    result = {
        "component_id": component_id,
        "present": True,
        "strategy": resolved.strategy,
        "bounds": list(capture_bounds),
        "screenshot": relative_screenshot,
        "window_activation": window_activation,
    }
    _record_component_assertion(
        ctx,
        "component_exists",
        component_id,
        passed=True,
        expected=True,
        actual=True,
        evidence=(expected_item, actual_state, actual_image),
        strategy=resolved.strategy,
        bounds=list(capture_bounds),
        window_activation=window_activation,
    )
    return result


@step("gui.object.state.assert", domain="gui", description="Assert normalized GUI state or a property.", capabilities={"components"}, outputs={"state": "$"})
def gui_object_state_assert(ctx: TestContext, component_id: str, state_name: str, expected: Any):
    handle = ctx.component(component_id)
    try:
        observed = handle.state()
        actual = observed.get(state_name)
    except Exception as exc:
        expected_item = EvidenceItem.value_item(
            "expected", "component_state", {state_name: expected},
            description="Expected component state",
        )
        actual_item = EvidenceItem.value_item(
            "actual", "component_state", {"available": False},
            description="Component state could not be observed",
        )
        diagnostic = EvidenceItem.value_item(
            "diagnostic", "exception", {"type": type(exc).__name__, "message": str(exc)},
        )
        _record_component_assertion(
            ctx,
            "component_state",
            component_id,
            passed=False,
            expected=expected,
            actual=None,
            evidence=(expected_item, actual_item, diagnostic),
            message="component state could not be observed",
            state_name=state_name,
        )
        raise

    passed = actual == expected
    expected_item = EvidenceItem.value_item(
        "expected", "component_state", {state_name: expected},
        description="Expected component state",
    )
    actual_item = EvidenceItem.value_item(
        "actual", "component_state", observed.to_dict(),
        description="Observed component state",
    )
    _record_component_assertion(
        ctx,
        "component_state",
        component_id,
        passed=passed,
        expected=expected,
        actual=actual,
        evidence=(expected_item, actual_item),
        state_name=state_name,
    )
    if not passed:
        raise AssertionError(
            "component %r state mismatch: %s: expected %r, actual %r"
            % (component_id, state_name, expected, actual)
        )
    return observed


@step("gui.button.click", domain="gui", description="Convenience alias for a semantic click.", capabilities={"components"}, risk="application_control", outputs={"execution": "$"})
def gui_button_click(ctx: TestContext, component_id: str):
    return gui_object_action(ctx, component_id, "click")


@step("gui.text.set", domain="gui", description="Convenience alias for semantic text entry.", capabilities={"components"}, risk="application_control", outputs={"execution": "$"})
def gui_text_set(ctx: TestContext, component_id: str, value: str):
    return gui_object_action(ctx, component_id, {"type": "set_text", "value": value})


@step("gui.selection.select", domain="gui", description="Convenience alias for semantic item selection.", capabilities={"components"}, risk="application_control", outputs={"execution": "$"})
def gui_selection_select(ctx: TestContext, component_id: str, selector: Mapping[str, Any]):
    return gui_object_action(ctx, component_id, {"type": "select_item", "selector": selector})
