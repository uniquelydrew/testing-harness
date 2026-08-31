from __future__ import annotations

from types import SimpleNamespace

import pytest

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.pointer_actions import click_bounds
from automation_harness.models.component import ComponentDefinition, ComponentStrategy, ResolvedComponent
from automation_harness.models.gui import ActionType, ObjectType
from automation_harness.runner import live_cli


class _Evidence:
    def __init__(self):
        self.events = []

    def record(self, event, **payload):
        self.events.append((event, payload))


def _context():
    return SimpleNamespace(evidence=_Evidence(), target_application=None)


def test_bounds_resolvable_visual_object_exposes_click():
    definition = ComponentDefinition(
        component_id="map.blip",
        object_type=ObjectType.CUSTOM,
        actions=frozenset({"resolve"}),
        strategies=(
            ComponentStrategy(
                "anchored_visual",
                {
                    "anchor_identification": {"mandatory": {"name": "Map"}},
                    "relative_bounds": [0.1, 0.2, 0.05, 0.05],
                },
            ),
        ),
    )
    assert ActionType.CLICK in definition.semantic_actions


def test_click_uses_resolved_bounds_not_accessibility_activate(monkeypatch):
    definition = ComponentDefinition(
        component_id="plain.panel",
        object_type=ObjectType.PANEL,
        actions=frozenset({"resolve"}),
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"name": "Panel"}}}),),
    )
    handle = ComponentHandle(_context(), definition)
    monkeypatch.setattr(
        handle,
        "resolve",
        lambda: ResolvedComponent(
            "plain.panel",
            "atspi",
            {"bounds": [100, 200, 40, 20]},
        ),
    )
    observed = {}

    def fake_click(bounds, action):
        observed["bounds"] = bounds
        observed["action"] = action
        return {"x": 120, "y": 210, "button": 1, "clicks": 1}

    monkeypatch.setattr("automation_harness.core.pointer_actions.click_bounds", fake_click)
    result = handle.execute("click")
    assert result.strategy == "pointer"
    assert observed["bounds"] == [100, 200, 40, 20]
    assert observed["action"] == ActionType.CLICK


def test_click_bounds_rejects_missing_geometry_before_injection():
    with pytest.raises(ValueError, match="bounds"):
        click_bounds(None)


def test_cli_extracts_environment_startup_script(tmp_path):
    script = tmp_path / "start-environment.sh"
    argv = ["plan", "run", "test.yaml", "--environment-script", str(script)]
    assert live_cli._extract_environment_script(argv) == script.resolve()


def test_cli_recognizes_only_execution_commands_for_environment_startup():
    assert live_cli._is_execution_command(["plan", "run", "test.yaml"])
    assert live_cli._is_execution_command(["run", "bundle"])
    assert not live_cli._is_execution_command(["plan", "validate", "test.yaml"])
