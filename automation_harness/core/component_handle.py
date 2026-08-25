from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.anchored_visual import AnchoredVisualDriver
from automation_harness.models.component import ComponentDefinition, ComponentState, ResolvedComponent
from automation_harness.utils.wait import wait_for as wait_for_value


class ComponentResolutionError(RuntimeError):
    pass


class UnsupportedComponentAction(RuntimeError):
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
        return self.state().get(name)

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
        if "activate" not in self.definition.actions:
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
        return self._atspi_operation("read text", "get_text")

    def set_text(self, value: str) -> dict[str, Any]:
        return self._atspi_operation("set text", "set_text", value)

    def get_selection(self) -> list[str]:
        return self._atspi_operation("read selection", "get_selection")

    def select_child(self, child_index: int) -> dict[str, Any]:
        return self._atspi_operation("select child", "select_child", child_index)

    def get_value(self) -> float:
        return self._atspi_operation("read value", "get_value")

    def set_value(self, value: float) -> dict[str, Any]:
        return self._atspi_operation("set value", "set_value", value)

    def _atspi_operation(self, label: str, method: str, *args: Any):
        errors: list[str] = []
        for strategy in self.definition.strategies:
            if strategy.type not in {"atspi", "java_accessibility"}:
                continue
            try:
                driver = AtspiDriver(self.context) if strategy.type == "atspi" else JavaAccessibilityDriver(self.context)
                result = getattr(driver, method)(*args, identification=strategy.options.get("identification"))
                self.context.evidence.record("component_atspi_operation", component_id=self.definition.component_id, operation=method, result=result)
                return result
            except Exception as exc:
                errors.append(f"atspi: {type(exc).__name__}: {exc}")
        raise ComponentResolutionError(f"unable to {label} for {self.definition.component_id!r}; " + "; ".join(errors or ["no AT-SPI strategy"]))

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
