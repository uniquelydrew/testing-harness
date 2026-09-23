"""Canonical runtime-observation to persistent-test-object transformation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from automation_harness.core.runtime_observation import Framework, RuntimeObservation
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType, classify_accessibility, default_actions


_TRANSIENT_JAVAFX_CLASSES = (
    "menubuttonskin",
    "menuitemcontainer",
    "contextmenucontent",
)


@dataclass(frozen=True)
class IdentificationProfile:
    mandatory: tuple[str, ...]
    assistive: tuple[str, ...] = ()
    allow_ordinal: bool = True


@dataclass(frozen=True)
class TestObjectProposal:
    definition: ComponentDefinition
    warnings: tuple[str, ...] = ()


DEFAULT_PROFILES: Mapping[Framework, IdentificationProfile] = {
    Framework.JAVAFX: IdentificationProfile(
        mandatory=("accessible_id", "semantic_role"),
        assistive=("name", "window_id", "application", "physical_class"),
    ),
    Framework.SWING: IdentificationProfile(
        mandatory=("accessible_id", "semantic_role"),
        assistive=("name", "window_id", "physical_class"),
    ),
    Framework.AWT: IdentificationProfile(
        mandatory=("accessible_id", "semantic_role"),
        assistive=("name", "window_id", "physical_class"),
    ),
    Framework.ATSPI: IdentificationProfile(
        mandatory=("accessible_id", "semantic_role"),
        assistive=("name", "application", "window_id"),
    ),
    Framework.RENDERED: IdentificationProfile(
        mandatory=("rendered_class", "track_identity_key", "track_identity_value"),
        assistive=("window_id", "physical_class"),
        allow_ordinal=False,
    ),
}


class TestObjectFactory:
    """The only service permitted to create persistent objects from capture."""

    __test__ = False

    def __init__(self, profiles: Mapping[Framework, IdentificationProfile] | None = None):
        self._profiles = dict(DEFAULT_PROFILES)
        if profiles:
            self._profiles.update(profiles)

    def materialize(
        self,
        observation: RuntimeObservation,
        *,
        display_name: str,
        repository=None,
        profile: IdentificationProfile | None = None,
        owner_object_id: str | None = None,
        ordinal: int | None = None,
    ) -> TestObjectProposal:
        if not display_name or not display_name.strip():
            raise ValueError("test object display name must not be empty")
        if owner_object_id is not None and repository is not None:
            contains = getattr(repository, "contains", None)
            if not callable(contains) or not contains(owner_object_id):
                raise ValueError(
                    "persistent object owner must resolve in the target repository"
                )
        observation = self._promote_semantics(observation)
        self._reject_transient(observation)
        try:
            profile = profile or self._profiles[observation.framework]
        except KeyError as exc:
            raise ValueError(
                "no identification profile is registered for framework %s"
                % observation.framework.value
            ) from exc

        mandatory, assistive = self._build_identity(observation, profile)
        if not mandatory:
            raise ValueError(
                "captured object exposes no mandatory identity evidence for %s"
                % observation.framework.value
            )
        if ordinal is not None:
            if not profile.allow_ordinal:
                raise ValueError(
                    "%s objects do not permit ordinal identity" % observation.framework.value
                )
            if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
                raise ValueError("ordinal must be a non-negative integer")

        identification: dict[str, Any] = {"mandatory": mandatory}
        if assistive:
            identification["assistive"] = assistive
        if ordinal is not None:
            identification["ordinal"] = ordinal

        object_type = self._object_type(observation)
        actions = {item.value for item in default_actions(object_type)} | {"resolve"}
        if object_type in {ObjectType.MENU_BAR, ObjectType.MENU, ObjectType.CONTEXT_MENU}:
            actions = {"resolve", ActionType.SELECT_MENU_ITEM.value}

        strategy_type = {
            Framework.JAVAFX: "javafx",
            Framework.SWING: "java_agent",
            Framework.AWT: "java_agent",
            Framework.RENDERED: "java_agent",
            Framework.ATSPI: "atspi",
        }[observation.framework]
        properties = {
            **dict(observation.properties),
            "capture_evidence": {
                "adapter": observation.evidence.adapter,
                "authoritative": observation.evidence.authoritative,
                "fallback_used": observation.evidence.fallback_used,
                "reasons": list(observation.evidence.reasons),
            },
            "physical_ancestry": [
                {
                    key: value for key, value in {
                        "native_class": item.native_class,
                        "role": item.role,
                        "name": item.name,
                        "stable_id": item.stable_id,
                    }.items() if value is not None
                }
                for item in observation.physical_ancestry
            ],
        }
        return TestObjectProposal(ComponentDefinition(
            component_id=display_name.strip(),
            description=observation.description or observation.name or "Captured object",
            strategies=(ComponentStrategy(strategy_type, {"identification": identification}),),
            actions=frozenset(actions),
            expected_states=self._expected_states(observation),
            object_type=object_type,
            properties=properties,
            framework=observation.framework.value,
            native_class=observation.physical_class,
            subobjects={
                str(key): dict(value)
                for key, value in observation.logical_children.items()
            },
            scope={
                "process_id": observation.process_id,
                "window": observation.window_id,
                "application": observation.application,
            },
            owner_object_id=owner_object_id,
        ))

    @staticmethod
    def _promote_semantics(observation: RuntimeObservation) -> RuntimeObservation:
        # Physical JavaFX menu buttons are disposable skins for a logical menu.
        # Promotion changes semantics, while physical_class remains diagnostics.
        native = str(observation.physical_class or "").rsplit(".", 1)[-1].casefold()
        if observation.framework is Framework.JAVAFX and native in {
            "menubutton", "splitmenubutton", "menubarbutton",
        }:
            from dataclasses import replace
            return replace(observation, semantic_role="menu")
        return observation

    @staticmethod
    def _reject_transient(observation: RuntimeObservation) -> None:
        native = str(observation.physical_class or "").casefold()
        if observation.framework is Framework.JAVAFX and any(
            marker in native for marker in _TRANSIENT_JAVAFX_CLASSES
        ):
            raise ValueError(
                "transient JavaFX skin object cannot be persisted; capture its logical owner"
            )

    @staticmethod
    def _object_type(observation: RuntimeObservation) -> ObjectType:
        native = str(observation.physical_class or "").rsplit(".", 1)[-1].casefold()
        if observation.framework is Framework.JAVAFX and native in {
            "menubutton", "splitmenubutton", "menubarbutton",
        }:
            return ObjectType.MENU
        return classify_accessibility(observation.semantic_role, observation.physical_class)

    @classmethod
    def _build_identity(cls, observation, profile):
        mandatory = {}
        assistive = {}
        for key in profile.mandatory:
            value = cls._identity_value(observation, key)
            if value not in (None, ""):
                mandatory[key] = value
        for key in profile.assistive:
            if key in mandatory:
                continue
            value = cls._identity_value(observation, key)
            if value not in (None, ""):
                assistive[key] = value
        return mandatory, assistive

    @staticmethod
    def _identity_value(observation, key):
        if hasattr(observation, key):
            return getattr(observation, key)
        return observation.properties.get(key)

    @staticmethod
    def _expected_states(observation):
        return {
            key: value for key, value in {
                "visible": observation.state.visible,
                "showing": observation.state.showing,
                "enabled": observation.state.enabled,
            }.items() if value is not None
        }
