from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.visual_baselines import (
    VisualBaselineError, VisualProfile, approve_visual_candidate, reject_visual_candidate,
    select_visual_variant, stage_visual_candidate,
)
from automation_harness.models.component import ComponentDefinition, ComponentStrategy


def _repository(path: Path) -> ComponentRepository:
    definition = ComponentDefinition(
        component_id="tracking.canvas", strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"name": "Canvas"}}}),),
        actions=frozenset({"resolve"}),
    )
    repository = ComponentRepository({definition.component_id: definition})
    repository.save(path)
    return ComponentRepository.load([path])


def test_stage_then_approve_preserves_semantic_revision_and_creates_variant(tmp_path: Path):
    path = tmp_path / "components.yaml"
    definition = _repository(path).get("tracking.canvas")
    profile = VisualProfile({"os": "linux", "arch": "x86_64", "scale": "100", "color_scheme": "light", "session": "x11"})
    staged = stage_visual_candidate(path, definition, (4, 5, 3, 2), profile=profile, image=Image.new("RGB", (3, 2), "blue"))
    assert Path(staged["candidate"]).is_file()
    approved = approve_visual_candidate(path, "tracking.canvas", profile.key)
    assert approved.revision == 1
    assert approved.visual["revision"] == 1
    variant = approved.visual["variants"][profile.key]
    assert (tmp_path / variant["image"]).is_file()
    key, selected = select_visual_variant(ComponentRepository.load([path]).get("tracking.canvas"), profile)
    assert key == profile.key
    assert selected["component_revision"] == 1


def test_reject_only_removes_staged_candidate(tmp_path: Path):
    path = tmp_path / "components.yaml"
    definition = _repository(path).get("tracking.canvas")
    profile = VisualProfile({"os": "linux", "arch": "x", "scale": "100", "color_scheme": "light", "session": "x11"})
    stage_visual_candidate(path, definition, (0, 0, 2, 2), profile=profile, image=Image.new("RGB", (2, 2)))
    reject_visual_candidate(path, "tracking.canvas", profile.key)
    assert not (tmp_path / "visual" / ".staging" / "tracking" / "canvas" / profile.key).exists()
    assert ComponentRepository.load([path]).get("tracking.canvas").visual is None


def test_visual_variant_requires_exact_profile(tmp_path: Path):
    path = tmp_path / "components.yaml"
    definition = _repository(path).get("tracking.canvas")
    profile = VisualProfile({"os": "linux", "arch": "x", "scale": "100", "color_scheme": "light", "session": "x11"})
    stage_visual_candidate(path, definition, (0, 0, 2, 2), profile=profile, image=Image.new("RGB", (2, 2)))
    approve_visual_candidate(path, "tracking.canvas", profile.key)
    with pytest.raises(VisualBaselineError, match="available"):
        select_visual_variant(ComponentRepository.load([path]).get("tracking.canvas"), VisualProfile.current({"os": "windows"}))


def test_repository_rejects_visual_paths_outside_visual_root():
    with pytest.raises(ComponentRepositoryError, match="under visual"):
        ComponentRepository.from_document({"version": 1, "components": {"x": {
            "actions": ["resolve"], "strategies": [{"type": "atspi", "identification": {"mandatory": {"name": "x"}}}],
            "visual": {"variants": {"test": {"image": "../bad.png", "profile": {"os": "x"}, "component_revision": 1}}},
        }}})
