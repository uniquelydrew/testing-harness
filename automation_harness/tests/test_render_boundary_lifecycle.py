from dataclasses import replace
from types import SimpleNamespace

import pytest

from automation_harness.core.capture_boundaries import annotate_capture_boundary
from automation_harness.core.captured_repository import materialize_capture
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService
from automation_harness.core.object_reparenting import reparent_leaf
from automation_harness.core.object_resolution import ObjectResolutionError, resolve_repository_object
from automation_harness.models.component import CapturedComponent, ComponentState, ComponentStrategy, ResolvedComponent
from automation_harness.steps.visual_assert_steps import gui_object_visual_assert
from automation_harness.drivers.vision_driver import VisionDriver


def _jogl_capture():
    return annotate_capture_boundary(CapturedComponent(
        name="Tactical Display", role="canvas", description=None,
        accessible_id="tactical-display", application="MSCT", window="MSCT Display",
        hierarchy=("javax.swing.JFrame", "javax.swing.JPanel", "com.jogamp.opengl.awt.GLCanvas"),
        actions=("resolve", "click"), bounds=(100, 200, 1000, 600),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={"capture_point": [600, 500]},
        authored_strategy=ComponentStrategy("java_agent", {"identification": {
            "mandatory": {"accessible_id": "tactical-display"},
            "assistive": {"native_class": "com.jogamp.opengl.awt.GLCanvas", "window": "MSCT Display"},
        }}),
        framework="swing", native_class="com.jogamp.opengl.awt.GLCanvas",
    ))


class _DefinitionService:
    def definition_from_capture(self, component_id, captured):
        service = object.__new__(HybridObjectCaptureService)
        return HybridObjectCaptureService.definition_from_capture(service, component_id, captured)


class _JavaAgent:
    def inspect(self, *, identification):
        return _jogl_capture()


class _ResolutionService:
    java_agent_driver = _JavaAgent()


def test_capture_save_reparent_recapture_reload_playback_and_assertion(tmp_path, monkeypatch):
    repository, visual, created = materialize_capture(
        _DefinitionService(), ComponentRepository({}), "Track", _jogl_capture(),
    )
    assert created == ("TrackSurface",)
    surface = repository.get("TrackSurface")
    assert surface.framework == "jogl"
    assert surface.strategies[0].type == "java_agent"
    assert visual.owner_object_id == surface.object_id

    second = replace(
        surface, component_id="SecondarySurface",
        object_id="f8e55bea-1b28-4caa-a80c-d47ac243d4ce",
    )
    repository = repository.with_component(second)
    repository, _old, moved_id = reparent_leaf(repository, visual.component_id, second.component_id)
    moved = repository.get(moved_id)
    assert moved.object_id == visual.object_id
    with pytest.raises(ObjectResolutionError, match="requires recapture"):
        resolve_repository_object(_ResolutionService(), repository, moved)

    recaptured_strategy = ComponentStrategy("anchored_visual", {
        "anchor_identification": second.strategies[0].options["identification"],
        "relative_bounds": [0.45, 0.45, 0.1, 0.1],
    })
    recaptured = replace(
        moved, strategies=(recaptured_strategy,),
        properties={"coordinate_space": "normalized-owner", "locator_status": "ready"},
        revision=moved.revision + 1,
    )
    repository = repository.with_component(recaptured)
    path = tmp_path / "objects.ahobjects"
    repository.save(path)
    loaded = ComponentRepository.load((path,))

    result = resolve_repository_object(_ResolutionService(), loaded, loaded.get(recaptured.object_id))

    assert result.bounds == (550, 470, 100, 60)
    assert result.owner.definition.object_id == second.object_id

    visual_definition = replace(loaded.get(recaptured.object_id), visual={
        "revision": 1,
        "variants": {"test": {
            "image": "visual/track/test.png",
            "pixel_tolerance": 12,
            "max_difference_ratio": 0.1,
        }},
    })
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    for name in ("actual.png", "expected.png", "diff.png"):
        (run_dir / name).write_bytes(b"evidence")
    monkeypatch.setattr(VisionDriver, "compare_baseline", lambda *args, **kwargs: SimpleNamespace(
        actual=run_dir / "actual.png", expected=run_dir / "expected.png",
        diff=run_dir / "diff.png", changed_pixels=5, compared_pixels=100,
        difference_ratio=0.05,
    ))

    class Handle:
        definition = visual_definition

        def resolve(self):
            return ResolvedComponent(recaptured.object_id, "anchored_visual", {"bounds": list(result.bounds)})

    class Evidence:
        def record(self, *_args, **_kwargs):
            return None

    class Context:
        capabilities = frozenset({"components", "screen-capture"})

        def __init__(self):
            self.run_dir = run_dir
            self.evidence = Evidence()

        def component(self, reference):
            assert reference == recaptured.object_id
            return Handle()

    assertion = gui_object_visual_assert.__wrapped__(
        Context(), recaptured.object_id, "test", minimum_match_percentage=90.0,
    )
    assert assertion["passed"] is True
    assert assertion["match_percentage"] == 95.0
