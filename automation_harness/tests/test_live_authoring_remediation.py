from __future__ import annotations

import pytest

from automation_harness.authoring.project import AuthoringProject
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.locator_matching import _javafx_node_matches, _matches_value


def _component_document(locator_value):
    return {
        "version": 2,
        "components": {
            "dynamic_button": {
                "object_type": "button",
                "actions": ["click"],
                "strategies": [
                    {
                        "type": "atspi",
                        "identification": {
                            "mandatory": {
                                "accessible_id": locator_value,
                            }
                        },
                    }
                ],
            }
        },
    }


def test_regex_locator_value_is_valid_repository_identity():
    repository = ComponentRepository.from_document(
        _component_document({"regex": r"submit-[0-9]+"})
    )
    identity = repository.get("dynamic_button").strategies[0].options["identification"]
    assert identity["mandatory"]["accessible_id"] == {"regex": r"submit-[0-9]+"}


def test_invalid_regex_is_rejected_when_repository_is_loaded():
    with pytest.raises(ComponentRepositoryError, match="regex is invalid"):
        ComponentRepository.from_document(_component_document({"regex": "["}))


def test_regex_uses_full_match_semantics():
    assert _matches_value("submit-42", {"regex": r"submit-[0-9]+"})
    assert not _matches_value("prefix-submit-42", {"regex": r"submit-[0-9]+"})


def test_exact_matching_remains_unchanged():
    assert _matches_value("submit-42", "submit-42")
    assert not _matches_value("submit-42", "submit-43")


def test_case_insensitive_role_matching_supports_regex():
    assert _matches_value("Push Button", {"regex": r"push button"}, case_insensitive=True)


def test_javafx_nested_properties_support_regex():
    node = {
        "id": "result-20260831-42",
        "properties": {"automation.dynamic-id": "row-8842"},
        "parent": {"class": "javafx.scene.layout.VBox"},
    }
    assert _javafx_node_matches(
        node,
        {
            "id": {"regex": r"result-[0-9]{8}-[0-9]+"},
            "properties": {"automation.dynamic-id": {"regex": r"row-[0-9]+"}},
            "parent": {"class": "javafx.scene.layout.VBox"},
        },
    )


def test_live_desktop_backend_is_explicitly_targetless():
    backend = LiveDesktopBackend()
    assert backend.name == "live-desktop"
    details = backend.health_check().details
    assert "target_application" not in details
    assert details["desktop_session"] == "current"


def test_authoring_project_does_not_persist_legacy_target(tmp_path):
    project = AuthoringProject(
        name="demo",
        root=tmp_path,
        repository=tmp_path / "components.yaml",
        runs_dir=tmp_path / "runs",
        target={"kind": "attached-desktop", "expected_application": "Legacy App"},
    )
    document = project.to_document()
    assert "target" not in document
