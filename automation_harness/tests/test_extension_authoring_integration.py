"""Regression coverage for profile-aware catalog and action authoring."""
from __future__ import annotations

from pathlib import Path

import pytest

from automation_harness.authoring.action_catalog import action_by_id, actions_for
from automation_harness.core.component_catalog import (
    canonical_action_types, component_spec, display_name,
)
from automation_harness.extensions import (
    ExtensionFormatError, ExtensionRegistry, bind_extension, build_extension,
)
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType


def _registry(tmp_path: Path) -> ExtensionRegistry:
    root = Path(__file__).resolve().parents[2] / "extension_packs"
    registry = ExtensionRegistry(harness_version="0.5.2")
    registry.register(build_extension(root / "msct", tmp_path / "msct.ahext").path)
    registry.register(build_extension(root / "solipsys", tmp_path / "solipsys.ahext").path)
    return registry


def _definition(kind=ObjectType.PANEL, framework="swing") -> ComponentDefinition:
    return ComponentDefinition(
        "Monitor Panel", object_type=kind, framework=framework,
        strategies=(ComponentStrategy("java_agent", {
            "identification": {"mandatory": {"native_class": "javax.swing.JPanel"}},
        }),),
        actions=frozenset({"resolve", "click", "select_menu_item"}),
    )


def test_profiled_status_panel_has_no_fake_interaction_actions(tmp_path):
    registry = _registry(tmp_path)
    panel = _definition()
    bound = bind_extension(panel, "org.automation-harness.msct.messaging_status", registry)
    assert bound.object_id == panel.object_id
    assert bound.strategies == panel.strategies
    assert ActionType.CLICK in panel.semantic_actions
    actions = actions_for(bound, registry=registry)
    ids = [action.action_id for action in actions]
    assert "click" not in ids
    assert "select_menu_item" not in ids
    assert "read_fields" not in ids  # not yet an executable step
    assert ids == ["wait_for_state", "assert_exists", "assert_state", "read_property"]
    assert action_by_id(bound, "assert_exists", registry=registry).to_step_call(
        "assert-1", bound.object_id,
    ).inputs["component_id"] == bound.object_id
    with pytest.raises(ValueError, match="not available"):
        action_by_id(bound, "click", registry=registry)
    with pytest.raises(ExtensionFormatError, match="requires extension"):
        actions_for(bound)


def test_profiled_menu_owner_is_read_only_but_plain_menu_is_not(tmp_path):
    registry = _registry(tmp_path)
    owner = _definition(ObjectType.MENU)
    bound = bind_extension(owner, "org.automation-harness.msct.status_indicator", registry)
    assert "select_menu_item" not in {x.action_id for x in actions_for(bound, registry=registry)}
    assert "click" not in {x.action_id for x in actions_for(bound, registry=registry)}
    assert "select_menu_item" in {x.action_id for x in actions_for(owner)}


def test_opt_in_catalog_does_not_mutate_canonical_type(tmp_path):
    registry = _registry(tmp_path)
    profile_id = "org.automation-harness.msct.messaging_status"
    before = dict(component_spec(ObjectType.PANEL))
    assert component_spec(profile_id, registry=registry)["base_type"] == "panel"
    assert component_spec(profile_id, registry=registry)["interaction"]["selection"] is False
    assert canonical_action_types(profile_id, registry=registry) == frozenset()
    assert display_name(profile_id, registry=registry) == "Messaging Status"
    assert dict(component_spec(ObjectType.PANEL)) == before


def test_installed_profile_never_auto_binds_generic_capture(tmp_path):
    registry = _registry(tmp_path)
    panel = _definition()
    assert "click" in {action.action_id for action in actions_for(panel, registry=registry)}
    assert "click" in {action.action_id for action in actions_for(panel)}
