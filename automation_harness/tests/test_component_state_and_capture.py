from __future__ import annotations

from pathlib import Path

import pytest

from automation_harness.authoring.app import _highlight_rectangles
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.object_capture import ObjectCaptureService, _visual_region_at_point
from automation_harness.drivers.atspi_driver import _matches_criteria
from automation_harness.models.component import CapturedComponent, ComponentState


def test_component_state_distinguishes_unknown_from_false():
    state = ComponentState(present=True, enabled=False, expanded=None)
    assert state.get("enabled") is False
    assert state.get("expanded") is None


def test_capture_next_click_delegates_to_accessibility_driver():
    captured = CapturedComponent(
        name="Follow", role="push button", description="", accessible_id=None,
        application=None, hierarchy=(), actions=(), bounds=None,
        state=ComponentState(present=True), backend_properties={},
    )

    class ClickDriver:
        available = True

        def capture_next_click(self, *, timeout: float):
            assert timeout == 7.5
            return captured

    assert ObjectCaptureService(driver=ClickDriver()).capture_next_click(timeout=7.5) is captured


def test_capture_scoped_at_point_delegates_coordinates():
    captured = CapturedComponent(
        name="Follow", role="push button", description="", accessible_id=None,
        application="Demo", hierarchy=(), actions=(), bounds=(10, 20, 30, 40),
        state=ComponentState(present=True), backend_properties={},
    )

    class PointDriver:
        available = True

        def capture_scoped_at_point(self, x: int, y: int):
            assert (x, y) == (123, 456)
            return captured

    assert ObjectCaptureService(driver=PointDriver()).capture_scoped_at_point(123, 456) is captured


def test_highlight_rectangles_outline_component_bounds():
    assert _highlight_rectangles((10, 20, 100, 50), thickness=4) == (
        (10, 20, 100, 4), (10, 66, 100, 4), (10, 20, 4, 50), (106, 20, 4, 50),
    )
    with pytest.raises(ValueError, match="positive"):
        _highlight_rectangles((10, 20, 0, 50))


def test_stale_atspi_proxy_is_not_a_matching_candidate():
    class StaleProxy:
        @property
        def name(self):
            raise RuntimeError("defunct object")

    assert _matches_criteria(StaleProxy(), {"name": "Follow"}) is False


def test_visual_region_capture_bridges_control_interior_to_border(monkeypatch):
    from PIL import Image, ImageDraw, ImageGrab

    image = Image.new("RGB", (100, 60), (244, 244, 244))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 20, 89, 39), outline=(190, 190, 190))
    draw.rectangle((13, 23, 59, 36), fill=(0, 149, 199))
    monkeypatch.setattr(ImageGrab, "grab", lambda: image)

    assert _visual_region_at_point((0, 0, 100, 60), 30, 30) == (10, 20, 80, 20)


def test_captured_component_can_be_persisted_and_revisioned(tmp_path: Path):
    class OfflineDriver:
        available = False

    captured = CapturedComponent(
        name="Follow",
        role="push button",
        description="Follow selected track",
        accessible_id="follow-button",
        application="Reference",
        hierarchy=("Reference", "Toolbar", "Follow"),
        actions=("click",),
        bounds=(10, 20, 90, 30),
        state=ComponentState(present=True, visible=True, enabled=False),
        backend_properties={"class": "button"},
    )
    # Persistence is independent of whether this test host happens to have a
    # live AT-SPI desktop.  Runtime identity validation belongs to the
    # accessibility integration tests.
    service = ObjectCaptureService(driver=OfflineDriver())
    path = tmp_path / "components.yaml"
    first = service.save_capture(
        path,
        "tracking.follow_button",
        captured,
        criteria={"accessible_id": "follow-button", "role": "push button"},
    )
    second = service.save_capture(
        path,
        "tracking.follow_button",
        captured,
        criteria={"accessible_id": "follow-button", "role": "push button"},
    )
    repository = ComponentRepository.load([path])
    definition = repository.get("tracking.follow_button")
    assert first.revision == 1
    assert second.revision == 2
    assert definition.revision == 2
    assert definition.expected_states["enabled"] is False
    assert "activate" in definition.actions
    assert definition.strategies[0].options["identification"]["mandatory"]["accessible_id"] == "follow-button"


def test_capture_builds_rich_multi_property_identity():
    captured = CapturedComponent(
        name="Follow",
        role="push button",
        description="Follow selected track",
        accessible_id="follow-button",
        application="Reference Application",
        hierarchy=("Reference Application", "Tracking Window", "Tracking", "Follow"),
        actions=("click",),
        bounds=(10, 20, 90, 30),
        state=ComponentState(present=True, visible=True, enabled=True),
        backend_properties={},
        window="Tracking Window",
        parent_name="Tracking",
        parent_role="tool bar",
        parent_accessible_id="tracking-toolbar",
    )
    identity = captured.candidate_identification()
    assert identity.mandatory == {
        "accessible_id": "follow-button",
        "role": "push button",
    }
    assert identity.assistive["name"] == "Follow"
    assert identity.assistive["application"] == "Reference Application"
    assert identity.assistive["window"] == "Tracking Window"
    assert identity.assistive["parent"] == {
        "accessible_id": "tracking-toolbar",
        "name": "Tracking",
        "role": "tool bar",
    }


def test_repository_normalizes_legacy_flat_atspi_locator(tmp_path: Path):
    path = tmp_path / "legacy.yaml"
    path.write_text(
        """version: 1\ncomponents:\n  x:\n    actions: [resolve]\n    strategies:\n      - type: atspi\n        name: Follow\n        role: push button\n""",
        encoding="utf-8",
    )
    repository = ComponentRepository.load([path])
    identity = repository.get("x").strategies[0].options["identification"]
    assert identity == {"mandatory": {"name": "Follow", "role": "push button"}}


def test_automatic_capture_refuses_identity_that_remains_ambiguous():
    from automation_harness.drivers.atspi_driver import AtspiResolutionStage

    class AmbiguousDriver:
        available = True

        def assess_identification(self, identification):
            criteria = dict(identification.mandatory)
            stages = [AtspiResolutionStage("mandatory", dict(criteria), 2)]
            for key, value in identification.assistive.items():
                criteria[key] = value
                stages.append(AtspiResolutionStage(f"assistive:{key}", dict(criteria), 2))
            return tuple(stages)

    captured = CapturedComponent(
        name="Save",
        role="push button",
        description=None,
        accessible_id=None,
        application="Editor",
        hierarchy=("Editor", "Main", "Save"),
        actions=("click",),
        bounds=None,
        state=ComponentState(present=True),
        window="Main",
    )
    service = ObjectCaptureService(driver=AmbiguousDriver())
    import pytest
    with pytest.raises(ValueError, match="remains ambiguous"):
        service.definition_from_capture("editor.save", captured)


def test_automatic_capture_persists_assistive_identity_even_when_mandatory_is_unique():
    from automation_harness.drivers.atspi_driver import AtspiResolutionStage

    class UniqueDriver:
        available = True

        def assess_identification(self, identification):
            return (AtspiResolutionStage("mandatory", dict(identification.mandatory), 1),)

    captured = CapturedComponent(
        name="Follow",
        role="push button",
        description=None,
        accessible_id="follow-id",
        application="Reference",
        hierarchy=("Reference", "Tracking", "Follow"),
        actions=("click",),
        bounds=None,
        state=ComponentState(present=True),
        window="Tracking",
        parent_name="Toolbar",
        parent_role="tool bar",
    )
    definition = ObjectCaptureService(driver=UniqueDriver()).definition_from_capture("tracking.follow", captured)
    identity = definition.strategies[0].options["identification"]
    assert identity["mandatory"] == {"accessible_id": "follow-id", "role": "push button"}
    assert identity["assistive"]["name"] == "Follow"
    assert identity["assistive"]["application"] == "Reference"
    assert identity["assistive"]["window"] == "Tracking"
    assert identity["assistive"]["parent"] == {"name": "Toolbar", "role": "tool bar"}
