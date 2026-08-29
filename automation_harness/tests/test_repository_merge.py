from __future__ import annotations

from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.repository_merge import component_diff, definitions_equal


def _definition(source, description="Camera selector"):
    repository = ComponentRepository.from_document(
        {
            "version": 2,
            "components": {
                "camera_selector": {
                    "description": description,
                    "revision": 1,
                    "object_type": "button",
                    "actions": ["resolve", "activate"],
                    "framework": "javafx",
                    "native_class": "javafx.scene.control.Button",
                    "strategies": [
                        {
                            "type": "javafx",
                            "identification": {
                                "mandatory": {"id": "cameraSelectorButton"}
                            },
                        }
                    ],
                }
            },
        },
        source=source,
    )
    return repository.get("camera_selector")


def test_semantic_equality_ignores_repository_source_path():
    left = _definition("/tmp/current.yaml")
    right = _definition("/tmp/incoming.yaml")
    assert left.repository_path != right.repository_path
    assert definitions_equal("camera_selector", left, right)


def test_component_diff_only_reports_persisted_definition_changes():
    current = _definition("/tmp/current.yaml", description="Camera selector")
    incoming = _definition("/tmp/incoming.yaml", description="Select active camera")
    diff = component_diff("camera_selector", current, incoming)
    assert "--- current/camera_selector" in diff
    assert "+++ incoming/camera_selector" in diff
    assert "Camera selector" in diff
    assert "Select active camera" in diff
    assert "/tmp/current.yaml" not in diff
    assert "/tmp/incoming.yaml" not in diff
    assert "object_id" not in diff
