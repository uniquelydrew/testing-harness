from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from automation_harness.models.gui import ActionType, ObjectType, classify_accessibility, default_actions


@dataclass(frozen=True)
class ComponentStrategy:
    """One ordered mechanism for locating/interacting with a logical component."""

    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComponentDefinition:
    """Backend-neutral definition of a logical UI component.

    ``actions`` describes supported interaction capabilities. Transient object
    state is deliberately not part of identity; state is observed at runtime.
    """

    component_id: str
    description: str = ""
    strategies: tuple[ComponentStrategy, ...] = ()
    actions: frozenset[str] = frozenset({"resolve", "activate"})
    expected_states: Mapping[str, Any] = field(default_factory=dict)
    revision: int = 1
    # Persisted paths in ``visual`` are relative to this runtime-only source.
    visual: Mapping[str, Any] | None = None
    repository_path: Path | None = field(default=None, compare=False, repr=False)
    object_type: ObjectType = ObjectType.CUSTOM
    properties: Mapping[str, Any] = field(default_factory=dict)
    framework: str | None = None
    native_class: str | None = None
    subobjects: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    @property
    def semantic_actions(self) -> frozenset[ActionType]:
        """Canonical v2 actions, with lossless v1 ``activate`` compatibility."""
        values: set[ActionType] = set()
        for action in self.actions:
            if action == "activate":
                values.update({ActionType.ACTIVATE, ActionType.CLICK})
                continue
            try:
                values.add(ActionType(action))
            except ValueError:
                continue
        return frozenset(values) or default_actions(self.object_type)

    def supports(self, action: ActionType) -> bool:
        return action in self.semantic_actions


@dataclass(frozen=True)
class AtspiIdentification:
    """Progressive AT-SPI identity for one logical object.

    Mandatory properties are conjunctive and are always applied. Assistive
    properties are also conjunctive but are added in declaration order only
    when the mandatory set leaves more than one runtime candidate. ``ordinal``
    is an explicit, zero-based final discriminator and is never inferred.
    """

    mandatory: Mapping[str, Any]
    assistive: Mapping[str, Any] = field(default_factory=dict)
    ordinal: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "mandatory": dict(self.mandatory),
        }
        if self.assistive:
            payload["assistive"] = dict(self.assistive)
        if self.ordinal is not None:
            payload["ordinal"] = self.ordinal
        return payload


@dataclass(frozen=True)
class ComponentState:
    """Backend-neutral snapshot of one UI object's observable state.

    ``None`` means the backend does not expose that state. It is not equivalent
    to ``False``. ``properties`` retains backend-neutral or application-specific
    values that do not warrant a dedicated first-class field.
    """

    present: bool
    visible: bool | None = None
    showing: bool | None = None
    enabled: bool | None = None
    focused: bool | None = None
    selected: bool | None = None
    checked: bool | None = None
    pressed: bool | None = None
    expanded: bool | None = None
    editable: bool | None = None
    readonly: bool | None = None
    active: bool | None = None
    sensitive: bool | None = None
    properties: Mapping[str, Any] = field(default_factory=dict)

    def get(self, name: str) -> Any:
        if hasattr(self, name) and name != "properties":
            return getattr(self, name)
        try:
            return self.properties[name]
        except KeyError as exc:
            raise KeyError(f"component state/property {name!r} is not available") from exc

    def to_dict(self) -> dict[str, Any]:
        return {
            "present": self.present,
            "visible": self.visible,
            "showing": self.showing,
            "enabled": self.enabled,
            "focused": self.focused,
            "selected": self.selected,
            "checked": self.checked,
            "pressed": self.pressed,
            "expanded": self.expanded,
            "editable": self.editable,
            "readonly": self.readonly,
            "active": self.active,
            "sensitive": self.sensitive,
            "properties": dict(self.properties),
        }


@dataclass(frozen=True)
class ResolvedComponent:
    """Resolution result suitable for evidence and interaction."""

    component_id: str
    strategy: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CapturedComponent:
    """Object-spy result before it is persisted into a repository."""

    name: str | None
    role: str | None
    description: str | None
    accessible_id: str | None
    application: str | None
    hierarchy: tuple[str, ...]
    actions: tuple[str, ...]
    bounds: tuple[int, int, int, int] | None
    state: ComponentState
    backend_properties: Mapping[str, Any] = field(default_factory=dict)
    window: str | None = None
    parent_name: str | None = None
    parent_role: str | None = None
    parent_accessible_id: str | None = None
    authored_strategy: ComponentStrategy | None = None
    object_type: ObjectType | None = None
    framework: str | None = None
    native_class: str | None = None
    logical_subobjects: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)

    def semantic_type(self) -> ObjectType:
        return self.object_type or classify_accessibility(self.role, self.native_class)

    def candidate_identification(self) -> AtspiIdentification:
        """Build a durable, multi-property identity from the capture.

        The identity deliberately keeps several stable conditions even when a
        smaller set happens to be unique in the current UI. Geometry and index
        are excluded because they are observations, not durable identity.
        """

        mandatory: dict[str, Any] = {}
        assistive: dict[str, Any] = {}

        if self.accessible_id:
            mandatory["accessible_id"] = self.accessible_id
            if self.role:
                mandatory["role"] = self.role
            if self.name:
                assistive["name"] = self.name
        else:
            if self.name:
                mandatory["name"] = self.name
            if self.role:
                mandatory["role"] = self.role

        if not mandatory and self.application:
            mandatory["application"] = self.application

        if self.application and "application" not in mandatory:
            assistive["application"] = self.application
        if self.window:
            assistive["window"] = self.window

        parent: dict[str, str] = {}
        if self.parent_accessible_id:
            parent["accessible_id"] = self.parent_accessible_id
        if self.parent_name:
            parent["name"] = self.parent_name
        if self.parent_role:
            parent["role"] = self.parent_role
        if parent:
            assistive["parent"] = parent

        if not mandatory:
            raise ValueError("captured object exposes no durable AT-SPI identification properties")
        return AtspiIdentification(mandatory=mandatory, assistive=assistive)

    def candidate_strategy(self) -> ComponentStrategy:
        if self.authored_strategy is not None:
            return self.authored_strategy
        return ComponentStrategy(
            "atspi",
            {"identification": self.candidate_identification().to_dict()},
        )

    def to_dict(self) -> dict[str, Any]:
        strategy = self.candidate_strategy()
        return {
            "name": self.name,
            "role": self.role,
            "description": self.description,
            "accessible_id": self.accessible_id,
            "application": self.application,
            "window": self.window,
            "parent_name": self.parent_name,
            "parent_role": self.parent_role,
            "parent_accessible_id": self.parent_accessible_id,
            "hierarchy": list(self.hierarchy),
            "actions": list(self.actions),
            "bounds": list(self.bounds) if self.bounds else None,
            "state": self.state.to_dict(),
            "backend_properties": dict(self.backend_properties),
            "candidate_strategy": {
                "type": strategy.type,
                **strategy.options,
            },
            "object_type": self.semantic_type().value,
            "framework": self.framework,
            "native_class": self.native_class,
            "logical_subobjects": {key: dict(value) for key, value in self.logical_subobjects.items()},
        }
