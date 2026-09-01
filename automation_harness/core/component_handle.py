from __future__ import annotations

import builtins
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from automation_harness.core.pointer_actions import click_bounds
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.drivers.anchored_visual import AnchoredVisualDriver
from automation_harness.models.component import ComponentDefinition, ComponentState, ResolvedComponent
from automation_harness.models.gui import ActionType, ExecutionResult, GuiAction, GuiState, ObjectIdentity
from automation_harness.utils.wait import wait_for as wait_for_value


class ComponentResolutionError(RuntimeError):
    pass


class UnsupportedComponentAction(RuntimeError):
    pass


class UnsupportedGuiAction(UnsupportedComponentAction):
    pass


class ComponentStateTimeout(TimeoutError):
    pass


@dataclass
class ComponentHandle:
    context: "TestContext"
    definition: ComponentDefinition

    def resolve(self) -> ResolvedComponent:
        errors: list[str] = []
        for strategy in self.definition.strategies:
            try:
                result = self._resolve_strategy(strategy.type, strategy.options)
                self.context.evidence.record(
                    "component_resolved",
                    component_id=self.definition.component_id,
                    strategy=result.strategy,
                    metadata=result.metadata,
                )
                return result
            except Exception as exc:
                errors.append(f"{strategy.type}: {type(exc).__name__}: {exc}")
                self.context.evidence.record(
                    "component_resolution_attempt_failed",
                    component_id=self.definition.component_id,
                    strategy=strategy.type,
                    error=f"{type(exc).__name__}: {exc}",
                )
        raise ComponentResolutionError(
            f"unable to resolve {self.definition.component_id!r}; " + "; ".join(errors)
        )

    def state(self) -> ComponentState:
        errors: list[str] = []
        for strategy in self.definition.strategies:
            try:
                result = self._state_strategy(strategy.type, strategy.options)
                self.context.evidence.record(
                    "component_state_observed",
                    component_id=self.definition.component_id,
                    strategy=strategy.type,
                    state=result.to_dict(),
                )
                return result
            except Exception as exc:
                errors.append(f"{strategy.type}: {type(exc).__name__}: {exc}")
                self.context.evidence.record(
                    "component_state_attempt_failed",
                    component_id=self.definition.component_id,
                    strategy=strategy.type,
                    error=f"{type(exc).__name__}: {exc}",
                )
        raise ComponentResolutionError(
            f"unable to inspect state for {self.definition.component_id!r}; " + "; ".join(errors)
        )

    def property(self, name: str) -> Any:
        if name in self.definition.properties:
            return self.definition.properties[name]
        return self.state().get(name)

    @builtins.property
    def identity(self) -> ObjectIdentity:
        return ObjectIdentity(self.definition.component_id, framework_id=self.definition.framework)

    @builtins.property
    def object_type(self):
        return self.definition.object_type

    def supports(self, action: ActionType | str) -> bool:
        return self.definition.supports(ActionType(action))

    def gui_state(self) -> GuiState:
        state = self.state()
        return GuiState(state.present, state.visible, state.enabled, state.focused, state.properties)

    def execute(self, action: GuiAction | ActionType | str | dict, *, strategy: str | None = None) -> ExecutionResult:
        """Execute one validated semantic action through the configured strategies."""
        semantic = GuiAction.from_value(action)
        if not self.supports(semantic.type):
            available = ", ".join(sorted(item.value for item in self.definition.semantic_actions)) or "none"
            raise UnsupportedGuiAction(
                f"object {self.definition.component_id!r} type {self.definition.object_type.value} does not support "
                f"{semantic.type.value}; supported actions: {available}"
            )
        if strategy not in {None, "pointer", "accessibility", "atspi", "java_accessibility", "javafx"}:
            raise ComponentResolutionError(f"execution strategy {strategy!r} is not available for this object")

        execution_strategy = strategy or "accessibility"
        try:
            if semantic.type in {ActionType.CLICK, ActionType.DOUBLE_CLICK, ActionType.RIGHT_CLICK}:
                if strategy not in {None, "pointer"}:
                    raise ComponentResolutionError(
                        f"{semantic.type.value} is a pointer action and cannot use strategy {strategy!r}"
                    )
                resolved = self.resolve()
                bounds = resolved.metadata.get("bounds")
                if bounds is None:
                    raise ComponentResolutionError(
                        f"resolved object {self.definition.component_id!r} has no screen bounds for pointer interaction"
                    )
                payload = click_bounds(bounds, semantic.type)
                payload["resolved_strategy"] = resolved.strategy
                execution_strategy = "pointer"
            elif semantic.type == ActionType.ACTIVATE:
                payload = self.activate()
            elif semantic.type == ActionType.SET_TEXT:
                if not isinstance(semantic.value, str):
                    raise ValueError("set_text requires a string value")
                payload = self.set_text(semantic.value)
            elif semantic.type == ActionType.CLEAR_TEXT:
                payload = self.set_text("")
            elif semantic.type == ActionType.APPEND_TEXT:
                if not isinstance(semantic.value, str):
                    raise ValueError("append_text requires a string value")
                payload = self.set_text(self.get_text() + semantic.value)
            elif semantic.type in {ActionType.SELECT, ActionType.SELECT_ITEM, ActionType.SELECT_ROW, ActionType.SELECT_CELL}:
                index = semantic.value
                if index is None and semantic.selector is not None:
                    index = semantic.selector.criteria.get("index")
                if not isinstance(index, int):
                    raise ValueError(f"{semantic.type.value} currently requires selector.criteria.index")
                payload = self.select_child(index)
            elif semantic.type == ActionType.SET_VALUE:
                if not isinstance(semantic.value, (int, float)) or isinstance(semantic.value, bool):
                    raise ValueError("set_value requires a numeric value")
                payload = self.set_value(float(semantic.value))
            else:
                raise ComponentResolutionError(
                    f"semantic action {semantic.type.value!r} is declared but has no executor for the current strategy chain"
                )
        except Exception as exc:
            self.context.evidence.record(
                "gui_action_attempt_failed",
                component_id=self.definition.component_id,
                action=semantic.to_dict(),
                strategy=execution_strategy,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        result = ExecutionResult(
            semantic.type,
            execution_strategy,
            payload,
            ({"strategy": execution_strategy, "success": True},),
        )
        self.context.evidence.record(
            "gui_action_executed",
            component_id=self.definition.component_id,
            action=semantic.to_dict(),
            strategy=result.strategy,
            result=dict(payload),
        )
        return result

    def assert_state(self, **expected: Any) -> ComponentState:
        observed = self.state()
        mismatches = {
            name: (value, observed.get(name))
            for name, value in expected.items()
            if observed.get(name) != value
        }
        if mismatches:
            details = ", ".join(
                f"{name}: expected {wanted!r}, actual {actual!r}"
                for name, (wanted, actual) in mismatches.items()
            )
            raise AssertionError(f"component {self.definition.component_id!r} state mismatch: {details}")
        return observed

    def assert_visual(self, *, profile=None):
        """Assert this component's framebuffer bounds match its approved visual gold."""
        from automation_harness.drivers.vision_driver import VisionDriver
        return VisionDriver(self.context).compare_component_baseline(self, profile=profile)

    def wait_for(self, *, timeout: float = 5.0, interval: float = 0.1, **expected: Any) -> ComponentState:
        last: ComponentState | None = None

        def predicate() -> bool:
            nonlocal last
            last = self.state()
            return all(last.get(name) == value for name, value in expected.items())

        try:
            wait_for_value(lambda: predicate(), timeout=timeout, interval=interval, description=f"{self.definition.component_id} state {expected}")
        except TimeoutError as exc:
            actual = last.to_dict() if last is not None else None
            raise ComponentStateTimeout(
                f"component {self.definition.component_id!r} did not reach {expected!r} within {timeout}s; "
                f"last observed state={actual!r}"
            ) from exc
        assert last is not None
        return last

    def activate(self) -> dict[str, Any]:
        if "activate" not in self.definition.actions and not self.supports(ActionType.ACTIVATE):
            raise UnsupportedComponentAction(
                f"component {self.definition.component_id!r} does not support activation"
            )

        errors: list[str] = []
        for strategy in self.definition.strategies:
            try:
                result = self._activate_strategy(strategy.type, strategy.options)
                self.context.evidence.record(
                    "component_activated",
                    component_id=self.definition.component_id,
                    strategy=strategy.type,
                    result=result,
                )
                return result
            except Exception as exc:
                errors.append(f"{strategy.type}: {type(exc).__name__}: {exc}")
                self.context.evidence.record(
                    "component_activation_attempt_failed",
                    component_id=self.definition.component_id,
                    strategy=strategy.type,
                    error=f"{type(exc).__name__}: {exc}",
                )
        raise ComponentResolutionError(
            f"unable to activate {self.definition.component_id!r}; " + "; ".join(errors)
        )

    def get_text(self) -> str:
        return self._accessibility_operation("read text", "get_text")

    def set_text(self, value: str) -> dict[str, Any]:
        return self._accessibility_operation("set text", "set_text", value)

    def get_selection(self) -> list[str]:
        return self._accessibility_operation("read selection", "get_selection")

    def select_child(self, child_index: int) -> dict[str, Any]:
        return self._accessibility_operation("select child", "select_child", child_index)

    def get_value(self) -> float:
        return self._accessibility_operation("read value", "get_value")

    def set_value(self, value: float) -> dict[str, Any]:
        return self._accessibility_operation("set value", "set_value", value)

    def _accessibility_operation(self, label: str, method: str, *args: Any):
        errors: list[str] = []
        for strategy in self.definition.strategies:
            if strategy.type not in {"atspi", "java_accessibility", "javafx"}:
                continue
            try:
                if strategy.type == "atspi":
                    driver = AtspiDriver(self.context)
                elif strategy.type == "java_accessibility":
                    driver = JavaAccessibilityDriver(self.context)
                else:
                    driver = JavaFxBridgeDriver(self.context)
                identification = strategy.options.get("identification")
                result = getattr(driver, method)(*args, identification=identification)
                self.context.evidence.record(
                    "component_accessibility_operation",
                    component_id=self.definition.component_id,
                    strategy=strategy.type,
                    operation=method,
                    result=result,
                )
                return result
            except Exception as exc:
                errors.append(f"{strategy.type}: {type(exc).__name__}: {exc}")
        raise ComponentResolutionError(
            f"unable to {label} for {self.definition.component_id!r}; "
            + "; ".join(errors or ["no compatible accessibility strategy"])
        )

    def _resolve_strategy(self, strategy_type: str, options: dict[str, Any]) -> ResolvedComponent:
        if strategy_type == "atspi":
            return AtspiDriver(self.context).resolve(
                self.definition.component_id,
                identification=options.get("identification"),
                name=_optional_str(options.get("name")),
                role=_optional_str(options.get("role")),
                accessible_id=_optional_str(options.get("accessible_id")),
            )
        if strategy_type == "java_accessibility":
            return JavaAccessibilityDriver(self.context).resolve(
                self.definition.component_id,
                identification=options.get("identification"),
            )
        if strategy_type == "javafx":
            return JavaFxBridgeDriver(self.context).resolve(
                self.definition.component_id,
                identification=options.get("identification"),
            )
        if strategy_type == "anchored_visual":
            return AnchoredVisualDriver(self.context).resolve(
                self.definition.component_id,
                anchor_identification=options.get("anchor_identification"),
                relative_bounds=options.get("relative_bounds"),
            )
        if strategy_type == "reference_inspection":
            reference = self.context.require_reference()
            component_id = str(options.get("component_id", self.definition.component_id))
            result = reference.request("ui_component", component_id=component_id)
            if not result.get("present", False):
                raise LookupError(f"reference UI component not present: {component_id}")
            return ResolvedComponent(
                component_id=self.definition.component_id,
                strategy="reference_inspection",
                metadata=dict(result),
            )
        raise ValueError(f"unsupported component strategy: {strategy_type}")

    def _state_strategy(self, strategy_type: str, options: dict[str, Any]) -> ComponentState:
        if strategy_type == "atspi":
            return AtspiDriver(self.context).state(
                identification=options.get("identification"),
                name=_optional_str(options.get("name")),
                role=_optional_str(options.get("role")),
                accessible_id=_optional_str(options.get("accessible_id")),
            )
        if strategy_type == "java_accessibility":
            return JavaAccessibilityDriver(self.context).state(identification=options.get("identification"))
        if strategy_type == "javafx":
            return JavaFxBridgeDriver(self.context).state(identification=options.get("identification"))
        if strategy_type == "anchored_visual":
            return AnchoredVisualDriver(self.context).state(
                anchor_identification=options.get("anchor_identification"),
                relative_bounds=options.get("relative_bounds"),
            )
        if strategy_type == "reference_inspection":
            reference = self.context.require_reference()
            component_id = str(options.get("component_id", self.definition.component_id))
            result = reference.request("ui_component", component_id=component_id)
            return ComponentState(
                present=bool(result.get("present", False)),
                visible=_optional_bool(result.get("visible")),
                showing=_optional_bool(result.get("showing")),
                enabled=_optional_bool(result.get("enabled")),
                focused=_optional_bool(result.get("focused")),
                selected=_optional_bool(result.get("selected")),
                checked=_optional_bool(result.get("checked")),
                pressed=_optional_bool(result.get("pressed")),
                expanded=_optional_bool(result.get("expanded")),
                editable=_optional_bool(result.get("editable")),
                readonly=_optional_bool(result.get("readonly")),
                active=_optional_bool(result.get("active")),
                sensitive=_optional_bool(result.get("sensitive")),
                properties={
                    key: value
                    for key, value in result.items()
                    if key not in {
                        "present", "visible", "showing", "enabled", "focused", "selected",
                        "checked", "pressed", "expanded", "editable", "readonly", "active", "sensitive",
                    }
                },
            )
        raise ValueError(f"unsupported component strategy: {strategy_type}")

    def _activate_strategy(self, strategy_type: str, options: dict[str, Any]) -> dict[str, Any]:
        if strategy_type == "atspi":
            return AtspiDriver(self.context).activate(
                identification=options.get("identification"),
                name=_optional_str(options.get("name")),
                role=_optional_str(options.get("role")),
                accessible_id=_optional_str(options.get("accessible_id")),
            )
        if strategy_type == "java_accessibility":
            return JavaAccessibilityDriver(self.context).activate(identification=options.get("identification"))
        if strategy_type == "javafx":
            return JavaFxBridgeDriver(self.context).activate(identification=options.get("identification"))
        if strategy_type == "reference_inspection":
            raise UnsupportedComponentAction(
                "reference_inspection is read-only and cannot activate UI components"
            )
        raise ValueError(f"unsupported component strategy: {strategy_type}")


def _optional_str(value: Any) -> str | None:
    return str(value) if value is not None else None


def _optional_bool(value: Any) -> bool | None:
    return bool(value) if value is not None else None


if TYPE_CHECKING:
    from automation_harness.core.test_context import TestContext
