from __future__ import annotations

from pathlib import Path

import pytest

from automation_harness.core.object_capture import ObjectCaptureService
from automation_harness.core.object_hierarchy import (
    compare_identity_evidence,
    condense_labels,
    hierarchy_contract,
)
from automation_harness.core.test_plan import load_plan
from automation_harness.models.component import (
    CapturedComponent,
    ComponentDefinition,
    ComponentState,
    ComponentStrategy,
)
from automation_harness.models.gui import ObjectType


def _captured(**overrides):
    values = dict(
        name="Save",
        role="push button",
        description="Save the document",
        accessible_id="save-button",
        application="Swing Editor",
        window="Editor",
        hierarchy=("Swing Editor", "JFrame", "JPanel", "Document", "Save"),
        actions=("click",),
        bounds=(10, 20, 80, 24),
        state=ComponentState(present=True, visible=True, enabled=True),
        backend_properties={"class": "javax.swing.JButton"},
        parent_name="Document",
        parent_role="panel",
        framework="swing",
        native_class="javax.swing.JButton",
    )
    values.update(overrides)
    return CapturedComponent(**values)


def test_hierarchy_condensation_preserves_concrete_runtime_containers():
    retained, removed = condense_labels(
        ("Editor", "JFrame", "JPanel", "Document", "Panel", "Save"),
        window="Editor",
    )
    assert retained == ("JFrame", "JPanel", "Document", "Panel", "Save")
    assert removed == ("Editor",)


def test_mixed_swing_javafx_lineage_keeps_bridge_and_largest_container():
    retained, removed = condense_labels(
        ("Editor", "JFrame", "JPanel", "JFXPanel", "BorderPane", "Save"),
        window="Editor",
    )
    assert retained == ("JFrame", "JPanel", "JFXPanel", "BorderPane", "Save")
    assert removed == ("Editor",)


def test_hierarchy_contract_uses_concrete_runtime_ancestry():
    contract = hierarchy_contract(_captured())
    assert contract["schema"] == "object-hierarchy/v2"
    assert contract["ownership"] == {
        "application": "Swing Editor",
        "window": "Editor",
        "framework": "swing",
    }
    assert [item["label"] for item in contract["path"]] == [
        "Swing Editor", "JFrame", "JPanel", "Document", "Save",
    ]
    assert contract["condensation"]["algorithm"] == "concrete-runtime-ancestry"
    assert contract["condensation"]["removed_count"] == 0


def test_recapture_comparison_classifies_stable_and_mutable_changes():
    result = compare_identity_evidence(
        {"mandatory": {"accessible_id": "save-button", "role": "push button"},
         "assistive": {"name": "Save", "window": "Editor"}},
        {"mandatory": {"accessible_id": "save-button", "role": "push button"},
         "assistive": {"name": "Save As", "window": "Editor 2"}},
    )
    assert result["mutable_changes"]["assistive.name"]["classification"] == "mutable"
    assert result["stable_changes"]["assistive.window"]["classification"] == "stable"


def test_click_count_returns_only_the_requested_click():
    captures = [_captured(name="first"), _captured(name="second"), _captured(name="target")]

    class Driver:
        available = True

        def __init__(self):
            self.calls = 0

        def capture_next_click(self, *, timeout):
            value = captures[self.calls]
            self.calls += 1
            return value

    driver = Driver()
    service = ObjectCaptureService(driver=driver)
    assert service.capture_next_click(click_count=3) is captures[-1]
    assert driver.calls == 3


def test_legacy_plan_aliases_are_migrated(tmp_path: Path):
    path = tmp_path / "old.ahplan"
    path.write_text(
        """title: Legacy desktop flow
version: 1
vars:
  username: drew
components: {}
calls:
  - node_id: open
    step_id: gui.object.action
    params:
      component_id: login.submit
      action:
        type: click
""",
        encoding="utf-8",
    )
    plan = load_plan(path)
    assert plan.name == "Legacy desktop flow"
    assert plan.variables["username"] == "drew"
    assert plan.steps[0].node_id == "open"
    assert plan.steps[0].step_id == "gui.object.action"
    assert plan.steps[0].inputs["component_id"] == "login.submit"


def test_recapture_preserves_object_identity_and_increments_revision():
    existing = ComponentDefinition(
        component_id="document.save",
        revision=4,
        object_type=ObjectType.BUTTON,
        actions=frozenset({"click"}),
        strategies=(ComponentStrategy(
            "atspi",
            {"identification": {"mandatory": {"accessible_id": "old", "role": "push button"}}},
        ),),
    )
    class OfflineDriver:
        available = False
    service = ObjectCaptureService(driver=OfflineDriver())
    updated, comparison = service.recapture_definition(
        existing,
        _captured(accessible_id="new"),
        validate_live=False,
    )
    assert updated.object_id == existing.object_id
    assert updated.revision == 5
    assert comparison["component_id"] == "document.save"
