from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.component import ComponentDefinition
from automation_harness.models.plan import TestPlan


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class RepositoryValidationIssue:
    code: str
    severity: ValidationSeverity
    message: str
    object_id: str | None = None
    component_id: str | None = None
    node_id: str | None = None


@dataclass(frozen=True)
class RepositoryValidationReport:
    issues: tuple[RepositoryValidationIssue, ...]

    @property
    def valid(self) -> bool:
        return not any(item.severity is ValidationSeverity.ERROR for item in self.issues)

    @property
    def errors(self) -> tuple[RepositoryValidationIssue, ...]:
        return tuple(item for item in self.issues if item.severity is ValidationSeverity.ERROR)


_SUPPORTED_STRATEGIES = {
    "atspi", "java_accessibility", "java_agent", "javafx",
    "anchored_visual", "reference_inspection",
}
_TRANSIENT_JAVAFX_CLASSES = {"MenuButtonSkin", "MenuItemContainer", "ContextMenuContent"}


def validate_repository(
    repository: ComponentRepository,
    *,
    plan: TestPlan | None = None,
) -> RepositoryValidationReport:
    issues: list[RepositoryValidationIssue] = []
    for definition in repository.components.values():
        issues.extend(_validate_definition(definition))
    if plan is not None:
        issues.extend(_validate_plan_references(repository, plan))
    return RepositoryValidationReport(tuple(issues))


def _validate_definition(definition: ComponentDefinition) -> Iterable[RepositoryValidationIssue]:
    common = {"object_id": definition.object_id, "component_id": definition.component_id}
    if definition.framework == "javafx" and _simple_class(definition.native_class) in _TRANSIENT_JAVAFX_CLASSES:
        yield RepositoryValidationIssue(
            "transient_javafx_class", ValidationSeverity.ERROR,
            "JavaFX skin/content implementation nodes cannot be durable test objects", **common,
        )
    for strategy in definition.strategies:
        if strategy.type not in _SUPPORTED_STRATEGIES:
            yield RepositoryValidationIssue(
                "unsupported_strategy", ValidationSeverity.ERROR,
                f"unsupported locator strategy {strategy.type!r}", **common,
            )
        identity = strategy.options.get("identification") if isinstance(strategy.options, Mapping) else None
        if strategy.type in {"atspi", "java_accessibility", "java_agent", "javafx"}:
            mandatory = identity.get("mandatory") if isinstance(identity, Mapping) else None
            if not isinstance(mandatory, Mapping) or not mandatory:
                yield RepositoryValidationIssue(
                    "empty_mandatory_locator", ValidationSeverity.ERROR,
                    f"{strategy.type} strategy requires at least one mandatory identity property", **common,
                )
    seen_subobjects: set[str] = set()
    for subobject_id, selector in definition.subobjects.items():
        if subobject_id in seen_subobjects:
            yield RepositoryValidationIssue(
                "duplicate_subobject_id", ValidationSeverity.ERROR,
                f"duplicate logical subobject id {subobject_id!r}", **common,
            )
        seen_subobjects.add(subobject_id)
        if not isinstance(selector, Mapping) or not selector:
            yield RepositoryValidationIssue(
                "empty_subobject_selector", ValidationSeverity.ERROR,
                f"logical subobject {subobject_id!r} requires a selector", **common,
            )


def _validate_plan_references(
    repository: ComponentRepository, plan: TestPlan,
) -> Iterable[RepositoryValidationIssue]:
    for call in plan.steps:
        value = call.inputs.get("component_id")
        if not isinstance(value, str) or not value:
            continue
        if not repository.contains(value):
            yield RepositoryValidationIssue(
                "dangling_plan_object_reference", ValidationSeverity.ERROR,
                f"plan step references unknown object {value!r}", node_id=call.node_id,
            )
        elif repository.get(value).object_id != value:
            yield RepositoryValidationIssue(
                "mutable_name_reference", ValidationSeverity.ERROR,
                "plan step must reference the immutable object UUID",
                object_id=repository.get(value).object_id,
                component_id=repository.get(value).component_id,
                node_id=call.node_id,
            )


def _simple_class(value: str | None) -> str | None:
    if value is None:
        return None
    return value.rsplit(".", 1)[-1]
