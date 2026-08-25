from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.models.component import AtspiIdentification, CapturedComponent, ComponentDefinition, ComponentStrategy
from automation_harness.core.visual_baselines import VisualProfile, stage_visual_candidate


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

    def capture_scoped_at_point(self, x: int, y: int) -> CapturedComponent:
        """Resolve the application at a point, then its deepest component."""
        captured = self.driver.capture_scoped_at_point(x, y)
        if (captured.role or "").casefold() in {"panel", "canvas", "drawing area"} and captured.bounds:
            visual_bounds = _visual_region_at_point(captured.bounds, x, y)
            if visual_bounds is not None and visual_bounds != captured.bounds:
                left, top, width, height = captured.bounds
                vx, vy, vw, vh = visual_bounds
                anchor = captured.candidate_identification().to_dict()
                strategy = ComponentStrategy("anchored_visual", {
                    "anchor_identification": anchor,
                    "relative_bounds": [
                        (vx - left) / width, (vy - top) / height, vw / width, vh / height,
                    ],
                })
                captured = replace(
                    captured,
                    name="Visual target",
                    role="visual component",
                    accessible_id=None,
                    actions=(),
                    bounds=visual_bounds,
                    backend_properties={
                        **dict(captured.backend_properties),
                        "anchor_bounds": list(captured.bounds),
                        "capture_point": [x, y],
                    },
                    authored_strategy=strategy,
                )
        return captured

    def capture_next_click(self, *, timeout: float = 30.0) -> CapturedComponent:
        """Capture the next accessible object selected by a desktop click."""
        return self.driver.capture_next_click(timeout=timeout)

    def resolve_anchored_visual(self, options: Mapping[str, Any]) -> CapturedComponent:
        anchor_identification = options.get("anchor_identification")
        relative = options.get("relative_bounds")
        if not isinstance(anchor_identification, Mapping):
            raise ValueError("anchored visual has no anchor identification")
        if not isinstance(relative, (list, tuple)) or len(relative) != 4:
            raise ValueError("anchored visual relative_bounds must contain four values")
        anchor = self.capture_by_locator(identification=anchor_identification)
        if anchor.bounds is None:
            raise LookupError("anchored visual container resolved without bounds")
        left, top, width, height = anchor.bounds
        rx, ry, rw, rh = (float(value) for value in relative)
        bounds = (
            left + round(rx * width), top + round(ry * height),
            max(1, round(rw * width)), max(1, round(rh * height)),
        )
        return replace(
            anchor, name="Visual target", role="visual component",
            accessible_id=None, actions=(), bounds=bounds,
            authored_strategy=ComponentStrategy("anchored_visual", dict(options)),
        )

    def capture_by_locator(
        self,
        *,
        identification: Mapping[str, Any] | None = None,
        name: str | None = None,
        role: str | None = None,
        accessible_id: str | None = None,
    ) -> CapturedComponent:
        return self.driver.inspect(
            identification=identification, name=name, role=role, accessible_id=accessible_id,
        )

    def assess(self, captured: CapturedComponent) -> tuple[LocatorAssessment, ...]:
        if captured.candidate_strategy().type == "anchored_visual":
            return (LocatorAssessment(
                source="accessible anchor + relative visual bounds",
                criteria=dict(captured.candidate_strategy().options),
                matches=1,
                stability={"anchor_identification": "high", "relative_bounds": "medium"},
            ),)
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
        authored = captured.candidate_strategy()
        if authored.type == "anchored_visual" and criteria is None and identification is None:
            return ComponentDefinition(
                component_id=component_id,
                description=description or captured.name or "Captured visual component",
                strategies=(authored,),
                actions=frozenset({"resolve"}),
                expected_states={"visible": True},
                revision=revision,
            )
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
            object_type=captured.semantic_type(),
            properties=dict(captured.backend_properties),
            framework=captured.framework,
            native_class=captured.native_class,
            subobjects=captured.logical_subobjects,
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

    def stage_visual_capture(
        self,
        path: Path,
        component_id: str,
        captured: CapturedComponent,
        *,
        profile: VisualProfile | None = None,
        pixel_tolerance: int = 12,
        max_difference_ratio: float = 0.01,
        image=None,
    ) -> dict[str, Any]:
        """Stage a component-bounds screenshot without altering repository metadata."""
        if captured.bounds is None:
            raise ValueError("captured object has no screen bounds for visual capture")
        repository = ComponentRepository.load([path])
        definition = repository.get(component_id)
        return stage_visual_candidate(
            path, definition, captured.bounds, profile=profile,
            pixel_tolerance=pixel_tolerance, max_difference_ratio=max_difference_ratio, image=image,
        )

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


def _visual_region_at_point(
    anchor_bounds: tuple[int, int, int, int], x: int, y: int,
) -> tuple[int, int, int, int] | None:
    """Find a non-background visual region connected to the clicked pixel."""
    try:
        from PIL import ImageGrab
    except ImportError:
        return None
    left, top, width, height = anchor_bounds
    if not (left <= x < left + width and top <= y < top + height):
        return None
    try:
        image = ImageGrab.grab().convert("RGB").crop((left, top, left + width, top + height))
    except Exception:
        return None
    # The most frequent quantized color is the container background. All
    # sufficiently different connected pixels form controls/canvases.
    histogram: dict[tuple[int, int, int], int] = {}
    sample = image.resize((max(1, width // 4), max(1, height // 4)))
    sample_pixels = sample.load()
    for sample_y in range(sample.height):
        for sample_x in range(sample.width):
            red, green, blue = sample_pixels[sample_x, sample_y]
            key = (red // 8, green // 8, blue // 8)
            histogram[key] = histogram.get(key, 0) + 1
    background_bin = max(histogram, key=histogram.get)
    background = tuple(value * 8 + 4 for value in background_bin)

    pixels = image.load()
    px, py = x - left, y - top
    threshold = 18

    def foreground(cx: int, cy: int) -> bool:
        color = pixels[cx, cy]
        return max(abs(color[index] - background[index]) for index in range(3)) >= threshold

    if not foreground(px, py):
        # Text or a tiny blank gap may be clicked; find the nearest foreground
        # seed without turning arbitrary container whitespace into a target.
        seeds = [
            (cx, cy)
            for radius in range(1, 9)
            for cx, cy in ((px-radius, py), (px+radius, py), (px, py-radius), (px, py+radius))
            if 0 <= cx < width and 0 <= cy < height and foreground(cx, cy)
        ]
        if not seeds:
            return None
        px, py = seeds[0]

    pending = [(px, py)]
    seen = {(px, py)}
    min_x = max_x = px
    min_y = max_y = py
    while pending:
        cx, cy = pending.pop()
        min_x, max_x = min(min_x, cx), max(max_x, cx)
        min_y, max_y = min(min_y, cy), max(max_y, cy)
        # Bridge antialiased/interior gaps up to three pixels. This joins a
        # progress fill to its enclosing border without merging neighboring
        # controls, whose layout spacing is materially larger.
        neighbors = (
            (cx + dx, cy + dy)
            for dx in range(-3, 4)
            for dy in range(-3, 4)
            if 0 < abs(dx) + abs(dy) <= 3
        )
        for nx, ny in neighbors:
            if (nx, ny) in seen or not (0 <= nx < width and 0 <= ny < height):
                continue
            if foreground(nx, ny):
                seen.add((nx, ny))
                pending.append((nx, ny))
    region_width, region_height = max_x - min_x + 1, max_y - min_y + 1
    if region_width < 6 or region_height < 6:
        return None
    return (left + min_x, top + min_y, region_width, region_height)


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
