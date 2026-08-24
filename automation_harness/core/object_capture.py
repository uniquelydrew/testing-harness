from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.models.component import AtspiIdentification, CapturedComponent, ComponentDefinition


@dataclass(frozen=True)
class LocatorAssessment:
    source: str
    criteria: dict[str, Any]
    matches: int
    stability: dict[str, Any]

    @property
    def unique(self) -> bool:
        return self.matches == 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "criteria": dict(self.criteria),
            "matches": self.matches,
            "unique": self.unique,
            "stability": dict(self.stability),
        }


class ObjectCaptureService:
    """Local Object Spy service used by both CLI and authoring GUI."""

    def __init__(self, driver: AtspiDriver | None = None) -> None:
        self.driver = driver or AtspiDriver()

    @property
    def available(self) -> bool:
        return self.driver.available

    def capture_at_point(self, x: int, y: int) -> CapturedComponent:
        return self.driver.capture_at_point(x, y)

    def capture_by_locator(
        self,
        *,
        name: str | None = None,
        role: str | None = None,
        accessible_id: str | None = None,
    ) -> CapturedComponent:
        return self.driver.inspect(name=name, role=role, accessible_id=accessible_id)

    def assess(self, captured: CapturedComponent) -> tuple[LocatorAssessment, ...]:
        identification = captured.candidate_identification()
        stages = self.driver.assess_identification(identification)
        return tuple(
            LocatorAssessment(
                source=stage.source,
                criteria=dict(stage.criteria),
                matches=stage.matches,
                stability=_criteria_stability(stage.criteria),
            )
            for stage in stages
        )

    def definition_from_capture(
        self,
        component_id: str,
        captured: CapturedComponent,
        *,
        description: str = "",
        criteria: Mapping[str, Any] | None = None,
        identification: AtspiIdentification | Mapping[str, Any] | None = None,
        revision: int = 1,
    ) -> ComponentDefinition:
        if criteria is not None and identification is not None:
            raise ValueError("supply criteria or identification, not both")
        if identification is None:
            if criteria is not None:
                identity = AtspiIdentification(mandatory=dict(criteria))
            else:
                identity = self._best_identification(captured)
        elif isinstance(identification, AtspiIdentification):
            identity = identification
        else:
            raw = dict(identification)
            mandatory = raw.get("mandatory", {})
            assistive = raw.get("assistive", {})
            ordinal = raw.get("ordinal")
            if not isinstance(mandatory, Mapping) or not isinstance(assistive, Mapping):
                raise ValueError("identification mandatory/assistive values must be mappings")
            if isinstance(ordinal, Mapping):
                ordinal = ordinal.get("index")
            if ordinal is not None and (not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0):
                raise ValueError("identification ordinal must be a non-negative integer")
            identity = AtspiIdentification(
                mandatory=dict(mandatory),
                assistive=dict(assistive),
                ordinal=ordinal,
            )
        if not identity.mandatory:
            raise ValueError("captured AT-SPI object requires at least one mandatory identification condition")
        if self.driver.available:
            stages = self.driver.assess_identification(identity)
            if not stages or stages[-1].matches == 0:
                raise ValueError("authored AT-SPI identity does not resolve the captured object")
            remaining = stages[-1].matches
            if remaining > 1 and identity.ordinal is None:
                raise ValueError(
                    f"authored AT-SPI identity remains ambiguous: {remaining} runtime objects match; "
                    "add assistive conditions or an explicit ordinal"
                )
            if identity.ordinal is not None and identity.ordinal >= remaining:
                raise ValueError(
                    f"authored AT-SPI ordinal {identity.ordinal} is outside {remaining} matching candidates"
                )

        actions = {"resolve"}
        action_names = {value.casefold() for value in captured.actions}
        if action_names & {"click", "press", "activate"}:
            actions.add("activate")
        expected = {
            key: value
            for key, value in captured.state.to_dict().items()
            if key in {"visible", "showing", "enabled"} and value is not None
        }
        from automation_harness.models.component import ComponentStrategy

        return ComponentDefinition(
            component_id=component_id,
            description=description or captured.description or captured.name or "Captured AT-SPI object",
            strategies=(ComponentStrategy("atspi", {"identification": identity.to_dict()}),),
            actions=frozenset(actions),
            expected_states=expected,
            revision=revision,
        )

    def save_capture(
        self,
        path: Path,
        component_id: str,
        captured: CapturedComponent,
        *,
        description: str = "",
        criteria: Mapping[str, Any] | None = None,
        identification: AtspiIdentification | Mapping[str, Any] | None = None,
    ) -> ComponentDefinition:
        repository = ComponentRepository.load([path]) if path.exists() else ComponentRepository({})
        old = repository.components.get(component_id)
        revision = (old.revision + 1) if old is not None else 1
        definition = self.definition_from_capture(
            component_id,
            captured,
            description=description,
            criteria=criteria,
            identification=identification,
            revision=revision,
        )
        repository.with_component(definition).save(path)
        return definition

    def _best_identification(self, captured: CapturedComponent) -> AtspiIdentification:
        identification = captured.candidate_identification()
        assessments = self.assess(captured)
        if not assessments:
            raise ValueError("captured object exposes no durable AT-SPI identification criteria")
        final = assessments[-1]
        if final.matches == 0:
            raise ValueError(
                "captured object identity no longer resolves while being assessed; recapture the object"
            )
        if final.matches > 1 and identification.ordinal is None:
            raise ValueError(
                f"captured object remains ambiguous: {final.matches} runtime objects satisfy the available "
                "mandatory and assistive conditions; add stable scope/identity properties or an explicit ordinal"
            )
        return identification


def _criteria_stability(criteria: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in criteria.items():
        if key == "parent" and isinstance(value, Mapping):
            result[key] = {child: _stability(child) for child in value}
        else:
            result[key] = _stability(key)
    return result


def _stability(key: str) -> str:
    return {
        "accessible_id": "very-high",
        "application": "high",
        "window": "high",
        "name": "high",
        "role": "high",
        "parent": "high",
        "hierarchy": "medium",
    }.get(key, "unknown")
