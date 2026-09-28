"""Materialize captured objects through one repository ownership policy."""
from __future__ import annotations

from dataclasses import replace

from automation_harness.core.capture_boundaries import classify_capture_boundary, surface_relative_visual_capture
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.semantic_hierarchy import materialize_semantic_ancestors
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ObjectType


def materialize_capture(
    service,
    repository: ComponentRepository,
    component_id: str,
    captured,
    *,
    visual_leaf: bool | None = None,
    validate_live: bool = True,
):
    """Create a concrete object, or a surface plus owner-relative visual leaf."""
    boundary = classify_capture_boundary(captured)
    has_point = isinstance(dict(captured.backend_properties or {}).get("capture_point"), (list, tuple))
    if visual_leaf is None:
        visual_leaf = boundary.supports_visual_children and has_point
    if visual_leaf and captured.candidate_strategy().type != "anchored_visual":
        captured = surface_relative_visual_capture(captured)
    definition = service.definition_from_capture(
        component_id, captured, validate_live=validate_live,
    )
    visual = next((item for item in definition.strategies if item.type == "anchored_visual"), None)
    if visual is None:
        repository, semantic_owner_id, created_ancestors = materialize_semantic_ancestors(
            repository, captured,
        )
        if (
            semantic_owner_id is not None
            and definition.owner_object_id is None
            and definition.object_type not in {ObjectType.WINDOW, ObjectType.DIALOG}
        ):
            definition = replace(definition, owner_object_id=semantic_owner_id)
        return (
            repository.with_component(definition),
            definition,
            created_ancestors,
        )

    anchor = visual.options.get("anchor_identification")
    matches = [
        candidate for candidate in repository.components.values()
        if any(
            strategy.type in {"atspi", "java_accessibility", "java_agent"}
            and strategy.options.get("identification") == anchor
            for strategy in candidate.strategies
        )
    ]
    if len(matches) > 1:
        raise ValueError("visual capture has multiple matching concrete rendering surfaces")
    created = []
    if matches:
        owner = matches[0]
    else:
        owner = _surface_definition(repository, component_id, captured, anchor)
        repository = repository.with_component(owner)
        created.append(owner.component_id)
    properties = dict(definition.properties or {})
    properties.update({"coordinate_space": "normalized-owner", "locator_status": "ready"})
    definition = replace(
        definition, owner_object_id=owner.object_id,
        properties=properties, framework="visual",
    )
    return repository.with_component(definition), definition, tuple(created)


def _surface_definition(repository, leaf_id, captured, anchor_identification):
    base = "%sSurface" % leaf_id.rsplit(".", 1)[-1]
    component_id = base
    suffix = 2
    while component_id in repository.components:
        component_id = "%s%s" % (base, suffix)
        suffix += 1
    properties = dict(captured.backend_properties or {})
    boundary = properties.get("capture_boundary")
    if not isinstance(boundary, dict) or not boundary.get("supports_visual_children"):
        raise ValueError("visual capture is not bound to a positively classified rendering surface")
    source = properties.get("surface_strategy")
    strategy_type = str(source.get("type")) if isinstance(source, dict) else "atspi"
    if strategy_type not in {"atspi", "java_accessibility", "java_agent"}:
        strategy_type = "atspi"
    framework = str(boundary.get("framework") or "custom-render")
    return ComponentDefinition(
        component_id=component_id,
        description="Concrete %s rendering surface" % framework,
        strategies=(ComponentStrategy(strategy_type, {"identification": dict(anchor_identification)}),),
        actions=frozenset({"resolve"}), expected_states={"visible": True},
        object_type=ObjectType.CANVAS, properties=properties,
        framework=framework, native_class=captured.native_class,
    )
