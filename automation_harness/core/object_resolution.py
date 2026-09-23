"""One evidence-driven resolver shared by authoring operations.

Repository highlighting, direct authoring, recording support code, and runtime
drivers must interpret a persisted object in the same way.  This module keeps
the framework dispatch and owner-relative visual geometry in one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.java_agent import JavaAgentDriver
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.models.component import CapturedComponent, ComponentDefinition


class ObjectResolutionError(LookupError):
    pass


@dataclass(frozen=True)
class ObjectResolution:
    definition: ComponentDefinition
    bounds: tuple[int, int, int, int]
    strategy: str
    captured: CapturedComponent | None = None
    owner: "ObjectResolution | None" = None


def resolve_repository_object(service, repository: ComponentRepository, definition: ComponentDefinition) -> ObjectResolution:
    """Resolve one exact repository object using its persisted strategy."""
    status = str(dict(definition.properties or {}).get("locator_status") or "ready")
    if status == "needs_recapture":
        raise ObjectResolutionError(
            "object locator requires recapture after its rendering-surface owner changed"
        )
    if status == "provisional":
        raise ObjectResolutionError(
            "object locator is provisional and cannot execute until durable identity is captured"
        )

    errors = []
    for strategy in definition.strategies:
        try:
            if strategy.type == "anchored_visual":
                return _resolve_visual(service, repository, definition, strategy.options)
            captured = resolve_live_capture(strategy.type, strategy.options, service=service)
            if captured.bounds is None:
                raise ObjectResolutionError("resolved object has no screen bounds")
            return ObjectResolution(
                definition=definition,
                bounds=tuple(int(round(float(value))) for value in captured.bounds),
                strategy=strategy.type,
                captured=captured,
            )
        except Exception as exc:
            errors.append("%s: %s: %s" % (strategy.type, type(exc).__name__, exc))
    raise ObjectResolutionError(
        "unable to resolve %r%s" % (
            definition.component_id,
            ("; " + "; ".join(errors)) if errors else "; object has no locator strategy",
        )
    )


def resolve_live_capture(strategy_type: str, options: Mapping, *, service=None, context=None):
    """Resolve one persisted native strategy for authoring and playback alike."""
    identification = options.get("identification")
    if strategy_type == "javafx":
        driver = service.javafx_driver if service is not None else JavaFxBridgeDriver(context)
        return driver.inspect(identification=identification)
    if strategy_type == "java_accessibility":
        return JavaAccessibilityDriver(context).inspect(identification=identification)
    if strategy_type == "java_agent":
        driver = service.java_agent_driver if service is not None and hasattr(service, "java_agent_driver") else JavaAgentDriver(context)
        return driver.inspect(identification=identification)
    if strategy_type == "atspi":
        if service is not None:
            return service.capture_by_locator(identification=identification)
        return AtspiDriver(context).inspect(identification=identification)
    raise ValueError("unsupported live locator strategy %r" % strategy_type)


def owner_relative_bounds(owner_bounds, relative_bounds):
    if not isinstance(owner_bounds, (list, tuple)) or len(owner_bounds) != 4:
        raise ObjectResolutionError("visual owner resolved without desktop bounds")
    if not isinstance(relative_bounds, (list, tuple)) or len(relative_bounds) != 4:
        raise ObjectResolutionError("visual relative_bounds must contain four values")
    left, top, width, height = (int(value) for value in owner_bounds)
    rx, ry, rw, rh = (float(value) for value in relative_bounds)
    if min(rx, ry, rw, rh) < 0 or rw <= 0 or rh <= 0 or rx + rw > 1.0001 or ry + rh > 1.0001:
        raise ObjectResolutionError("visual relative_bounds are outside its owner")
    return (
        left + round(rx * width), top + round(ry * height),
        max(1, round(rw * width)), max(1, round(rh * height)),
    )


def _resolve_visual(service, repository, definition, options):
    owner_id = definition.owner_object_id
    if not owner_id:
        raise ObjectResolutionError("visual object has no concrete owner_object_id")
    owner_definition = repository.get(owner_id)
    owner = resolve_repository_object(service, repository, owner_definition)
    relative = options.get("relative_bounds")
    if not isinstance(relative, (list, tuple)) or len(relative) != 4:
        raise ValueError("anchored visual relative_bounds must contain four values")
    bounds = owner_relative_bounds(owner.bounds, relative)
    return ObjectResolution(
        definition=definition,
        bounds=bounds,
        strategy="anchored_visual",
        owner=owner,
    )
