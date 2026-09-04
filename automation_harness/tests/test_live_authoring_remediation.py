from __future__ import annotations

from pathlib import Path

import pytest

from automation_harness.authoring.project import AuthoringProject
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.locator_matching import _javafx_node_matches, _matches_value
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver


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
        _component_document({"match": "regex", "value": r"submit-[0-9]+"})
    )
    identity = repository.get("dynamic_button").strategies[0].options["identification"]
    assert identity["mandatory"]["accessible_id"] == {
        "match": "regex",
        "value": r"submit-[0-9]+",
    }


def test_invalid_regex_is_rejected_when_repository_is_loaded():
    with pytest.raises(ComponentRepositoryError, match="regex is invalid"):
        ComponentRepository.from_document(
            _component_document({"match": "regex", "value": "["})
        )


def test_regex_uses_full_match_semantics():
    matcher = {"match": "regex", "value": r"submit-[0-9]+"}
    assert _matches_value("submit-42", matcher)
    assert not _matches_value("prefix-submit-42", matcher)


def test_exact_matching_remains_unchanged():
    assert _matches_value("submit-42", "submit-42")
    assert not _matches_value("submit-42", "submit-43")


def test_regex_mode_is_independent_for_each_property():
    assert _matches_value("Submit 42", {"match": "regex", "value": r"Submit [0-9]+"})
    assert _matches_value("primary-window", "primary-window")
    assert not _matches_value("primary-window-2", "primary-window")


def test_legacy_regex_shorthand_remains_loadable():
    assert _matches_value("submit-42", {"regex": r"submit-[0-9]+"})


def test_case_insensitive_role_matching_supports_regex():
    assert _matches_value(
        "Push Button",
        {"match": "regex", "value": r"push button"},
        case_insensitive=True,
    )


def test_javafx_nested_properties_support_regex():
    node = {
        "id": "result-20260831-42",
        "properties": {"automation.dynamic-id": "row-8842"},
        "parent": {"class": "javafx.scene.layout.VBox"},
    }
    assert _javafx_node_matches(
        node,
        {
            "id": {"match": "regex", "value": r"result-[0-9]{8}-[0-9]+"},
            "properties": {
                "automation.dynamic-id": {
                    "match": "regex",
                    "value": r"row-[0-9]+",
                }
            },
            "parent": {"class": "javafx.scene.layout.VBox"},
        },
    )


def test_live_desktop_backend_represents_only_the_execution_facility():
    backend = LiveDesktopBackend()
    assert backend.name == "live-desktop"
    details = backend.health_check().details
    assert details["desktop_session"] == "current"
    assert all("application" not in key for key in details)


def test_authoring_project_document_contains_no_execution_scope(tmp_path):
    project = AuthoringProject(
        name="demo",
        root=tmp_path,
        repository=tmp_path / "components.yaml",
        runs_dir=tmp_path / "runs",
    )
    document = project.to_document()
    assert set(document) == {"version", "name", "repository", "runs_dir"}


def test_authoring_core_contains_no_application_target_lifecycle():
    source = (Path(__file__).resolve().parents[1] / "authoring" / "app.py").read_text(encoding="utf-8")
    for obsolete in (
        "AttachedDesktopBackend",
        "AttachedExecutionBackend",
        "configure_target_dialog",
        "launch_target",
        "stop_target",
        "_target_backend",
        "_target_environment",
        "_attached_application",
        "expected_application",
        "environment_script",
    ):
        assert obsolete not in source


def test_runner_contains_no_attached_application_selector():
    runner = Path(__file__).resolve().parents[1] / "runner"
    cli_source = (runner / "cli.py").read_text(encoding="utf-8")
    execution_source = (runner / "plan_execution.py").read_text(encoding="utf-8")
    assert "attached-desktop" not in cli_source
    assert "--application" not in cli_source
    assert "target_application" not in execution_source
    assert "expected_application" not in execution_source


def test_component_resolution_cannot_inject_global_application_scope():
    source = (Path(__file__).resolve().parents[1] / "core" / "component_handle.py").read_text(encoding="utf-8")
    assert "_scoped_identification" not in source
    assert "target_application" not in source
    assert "attached target" not in source


def test_java_accessibility_driver_has_no_application_presence_gate():
    source = (Path(__file__).resolve().parents[1] / "drivers" / "java_accessibility.py").read_text(encoding="utf-8")
    assert "expected_application" not in source
    assert "application_present" not in source


def test_installed_javafx_regex_wrapper_preserves_process_scope():
    class Endpoint:
        def __init__(self, pid):
            self.pid = pid
            self.calls = []

        def request(self, operation, **payload):
            self.calls.append((operation, payload))
            return {
                "matches": [{"ref": "node-%s" % self.pid, "id": "fileMenu"}],
                "stages": [{
                    "source": "mandatory",
                    "criteria": payload["identification"].get("mandatory", {}),
                    "matches": 1,
                }],
            }

    covered = Endpoint(701)
    owner = Endpoint(702)

    class Driver(JavaFxBridgeDriver):
        def endpoints(self):
            return (covered, owner)

    matches, _trace = Driver()._find_matches(
        {"mandatory": {"id": {"match": "regex", "value": "file.*"}}},
        process_id=702,
    )

    assert [endpoint.pid for endpoint, _node in matches] == [702]
    assert covered.calls == []
    assert owner.calls[0][0] == "find"
