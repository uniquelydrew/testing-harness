from __future__ import annotations

import pytest

pytest.importorskip("gi")

from automation_harness.authoring.identity_editor import _flatten, _normalize_identity, _rebuild


def test_identity_form_preserves_nested_key_value_structure():
    identity = {
        "mandatory": {"class": "com.sun.javafx.scene.control.MenuBarButton"},
        "assistive": {
            "accessible_role": "MENU",
            "parent": {"class": "javafx.scene.layout.HBox"},
            "hierarchy": ["AnchorPane#AnchorPane", "MenuBar#topMenuBar", "HBox", "MenuBarButton"],
        },
        "ordinal": 2,
    }
    normalized = _normalize_identity(identity)
    assert normalized == identity

    leaves = dict(_flatten(identity["assistive"]["parent"]))
    assert leaves == {("class",): "javafx.scene.layout.HBox"}
    assert _rebuild(identity["assistive"]["parent"], leaves) == identity["assistive"]["parent"]

    hierarchy = identity["assistive"]["hierarchy"]
    hierarchy_leaves = dict(_flatten(hierarchy))
    assert hierarchy_leaves[(0,)] == "AnchorPane#AnchorPane"
    assert hierarchy_leaves[(3,)] == "MenuBarButton"
    assert _rebuild(hierarchy, hierarchy_leaves) == hierarchy


def test_identity_form_rejects_missing_mandatory_conditions():
    with pytest.raises(ValueError, match="mandatory"):
        _normalize_identity({"mandatory": {}, "assistive": {"window": "ERSA"}})


def test_identity_form_normalizes_legacy_ordinal_mapping():
    identity = {
        "mandatory": {"name": "File"},
        "ordinal": {"index": 1},
    }
    assert _normalize_identity(identity)["ordinal"] == 1
