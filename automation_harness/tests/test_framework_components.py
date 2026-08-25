from pathlib import Path

import pytest

from automation_harness.core.component_handle import ComponentHandle, UnsupportedComponentAction
from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError


def test_component_repository_allows_bundle_override(tmp_path: Path):
    base = tmp_path / "base.yaml"
    override = tmp_path / "override.yaml"
    base.write_text(
        """version: 1
components:
  demo.button:
    strategies:
      - type: atspi
        name: Old
        role: push button
""",
        encoding="utf-8",
    )
    override.write_text(
        """version: 1
components:
  demo.button:
    strategies:
      - type: atspi
        name: New
        role: push button
""",
        encoding="utf-8",
    )

    repository = ComponentRepository.load([base, override])
    definition = repository.get("demo.button")

    assert definition.strategies[0].options["identification"]["mandatory"]["name"] == "New"


def test_component_repository_parses_editor_document_without_a_file():
    repository = ComponentRepository.from_document(
        {
            "version": 1,
            "components": {
                "demo.button": {
                    "actions": ["resolve"],
                    "strategies": [{"type": "atspi", "name": "Save", "role": "push button"}],
                }
            },
        },
        source="editor",
    )

    assert repository.get("demo.button").strategies[0].options["identification"]["mandatory"]["name"] == "Save"


def test_component_repository_normalizes_java_accessibility_locator():
    repository = ComponentRepository.from_document(
        {"version": 1, "components": {"demo.java_button": {"actions": ["resolve", "activate"], "strategies": [{"type": "java_accessibility", "name": "Save", "role": "push button"}]}}},
        source="editor",
    )
    strategy = repository.get("demo.java_button").strategies[0]
    assert strategy.type == "java_accessibility"
    assert strategy.options["identification"]["mandatory"] == {"name": "Save", "role": "push button"}


def test_component_repository_normalizes_anchored_visual_locator():
    repository = ComponentRepository.from_document(
        {"version": 1, "components": {"demo.visual": {
            "actions": ["resolve"],
            "strategies": [{
                "type": "anchored_visual",
                "anchor_identification": {"mandatory": {"name": "Canvas", "role": "panel"}},
                "relative_bounds": [0.1, 0.2, 0.3, 0.4],
            }],
        }}},
        source="editor",
    )
    strategy = repository.get("demo.visual").strategies[0]
    assert strategy.options["anchor_identification"]["mandatory"] == {"name": "Canvas", "role": "panel"}
    assert strategy.options["relative_bounds"] == [0.1, 0.2, 0.3, 0.4]


def test_anchored_visual_is_read_only():
    with pytest.raises(ComponentRepositoryError, match="cannot declare activate"):
        ComponentRepository.from_document(
            {"version": 1, "components": {"demo.visual": {
                "actions": ["resolve", "activate"],
                "strategies": [{
                    "type": "anchored_visual",
                    "anchor_identification": {"mandatory": {"name": "Canvas", "role": "panel"}},
                    "relative_bounds": [0.1, 0.2, 0.3, 0.4],
                }],
            }}},
            source="editor",
        )


def test_component_repository_suggests_close_match(tmp_path: Path):
    path = tmp_path / "components.yaml"
    path.write_text(
        """version: 1
components:
  reference.threat.medium:
    strategies:
      - type: atspi
        name: Medium
        role: push button
""",
        encoding="utf-8",
    )
    repository = ComponentRepository.load([path])

    assert "reference.threat.medium" in repository.suggest("reference.threat.medum")


def test_legacy_reference_strategy_is_rejected(tmp_path: Path):
    path = tmp_path / "components.yaml"
    path.write_text(
        """version: 1
components:
  demo.button:
    strategies:
      - type: reference
        component_id: demo.button
""",
        encoding="utf-8",
    )

    with pytest.raises(ComponentRepositoryError, match="removed strategy 'reference'"):
        ComponentRepository.load([path])


def test_reference_inspection_cannot_be_declared_activatable(tmp_path: Path):
    path = tmp_path / "components.yaml"
    path.write_text(
        """version: 1
components:
  demo.canvas:
    actions: [resolve, activate]
    strategies:
      - type: reference_inspection
        component_id: demo.canvas
""",
        encoding="utf-8",
    )

    with pytest.raises(ComponentRepositoryError, match="cannot declare activate"):
        ComponentRepository.load([path])


def test_read_only_component_rejects_activation_before_strategy_execution():
    root = Path(__file__).resolve().parents[1]
    repository = ComponentRepository.load([root / "resources" / "components.yaml"])
    definition = repository.get("reference.track.canvas")
    handle = ComponentHandle(object(), definition)  # action check occurs before context access

    with pytest.raises(UnsupportedComponentAction, match="does not support activation"):
        handle.activate()


def test_built_in_activatable_components_have_no_reference_rpc_fallback():
    root = Path(__file__).resolve().parents[1]
    repository = ComponentRepository.load([root / "resources" / "components.yaml"])

    for definition in repository.components.values():
        if "activate" not in definition.actions:
            continue
        strategy_types = [strategy.type for strategy in definition.strategies]
        assert strategy_types == ["atspi"], definition.component_id
