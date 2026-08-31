from __future__ import annotations

import pytest

from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.component_repository import ComponentRepository, ComponentRepositoryError
from automation_harness.core.locator_matching import _matches_value


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


def test_live_desktop_backend_has_no_target_application():
    backend = LiveDesktopBackend()
    assert backend.name == "attached-desktop"
    assert backend.health_check().details["target_application"] is None
