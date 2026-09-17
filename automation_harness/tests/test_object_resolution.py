import pytest

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.object_resolution import ObjectResolutionError, resolve_repository_object
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy, ResolvedComponent


class _CaptureService:
    def capture_by_locator(self, *, identification):
        return CapturedComponent(
            name="surface", role="canvas", description=None,
            accessible_id="surface", application="Display", window="Display",
            hierarchy=("Display", "surface"), actions=(), bounds=(100, 200, 800, 600),
            state=ComponentState(present=True), native_class="com.jogamp.opengl.awt.GLCanvas",
        )


def _repository(status="ready"):
    surface = ComponentDefinition(
        component_id="Surface", framework="jogl",
        strategies=(ComponentStrategy("atspi", {
            "identification": {"mandatory": {"accessible_id": "surface"}},
        }),),
    )
    visual = ComponentDefinition(
        component_id="Surface.Track", owner_object_id=surface.object_id,
        strategies=(ComponentStrategy("anchored_visual", {
            "anchor_identification": {"mandatory": {"accessible_id": "surface"}},
            "relative_bounds": [0.25, 0.5, 0.1, 0.2],
        }),),
        properties={"locator_status": status},
    )
    return ComponentRepository({surface.component_id: surface, visual.component_id: visual}), visual


def test_visual_resolution_uses_current_concrete_owner_bounds():
    repository, visual = _repository()

    result = resolve_repository_object(_CaptureService(), repository, visual)

    assert result.strategy == "anchored_visual"
    assert result.bounds == (300, 500, 80, 120)
    assert result.owner.definition.component_id == "Surface"


def test_visual_resolution_rejects_locator_marked_for_recapture():
    repository, visual = _repository(status="needs_recapture")
    with pytest.raises(ObjectResolutionError, match="requires recapture"):
        resolve_repository_object(_CaptureService(), repository, visual)


def test_runtime_playback_resolves_visual_from_owner_identity(monkeypatch):
    repository, visual = _repository()

    class Context:
        components = repository

    monkeypatch.setattr(
        ComponentHandle,
        "resolve",
        lambda self: ResolvedComponent(
            self.definition.component_id, "atspi", {"bounds": [100, 200, 800, 600]},
        ),
    )
    strategy = visual.strategies[0]

    result = ComponentHandle(Context(), visual)._resolve_strategy(strategy.type, strategy.options)

    assert result.metadata["bounds"] == [300, 500, 80, 120]
    assert result.metadata["owner_object_id"] == visual.owner_object_id
