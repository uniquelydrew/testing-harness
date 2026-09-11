from __future__ import annotations

import os

import pytest

from automation_harness.core.javafx_transient_resolution import (
    install_javafx_transient_resolution,
    resolution_candidates,
)


LEGACY_CONTEXT_MENU_IDENTITY = {
    "mandatory": {"id": "cameraSelectorMenuItem"},
    "assistive": {
        "accessible_role": "MENU_ITEM",
        "class": "com.sun.javafx.scene.control.ContextMenuContent$MenuItemContainer",
        "hierarchy": [
            "CSSBridge",
            "ContextMenuContent",
            "MenuBox",
            "MenuItemContainer#cameraSelectorMenuItem",
        ],
        "style_classes": ["menu-item"],
        "window": "ContextMenu",
    },
}


def test_legacy_context_menu_identity_falls_back_to_durable_id():
    candidates = resolution_candidates(LEGACY_CONTEXT_MENU_IDENTITY)
    assert candidates[0] == LEGACY_CONTEXT_MENU_IDENTITY
    assert candidates[-1] == {"mandatory": {"id": "cameraSelectorMenuItem"}}
    assert "class" not in candidates[1].get("assistive", {})
    assert "hierarchy" not in candidates[1].get("assistive", {})
    assert "style_classes" not in candidates[1].get("assistive", {})


def test_runtime_resolution_accepts_recreated_context_menu_skin(monkeypatch):
    monkeypatch.setenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_TIMEOUT", "0")

    class Driver:
        _transient_resolution_policy_installed = False

        def _find_matches(self, identification, *, process_id=None):
            assistive = dict((identification or {}).get("assistive") or {})
            if assistive:
                # The popup was recreated and no longer has the recorded private
                # skin class/hierarchy, but the authored MenuItem id is stable.
                return [], ()
            if (identification or {}).get("mandatory") == {"id": "cameraSelectorMenuItem"}:
                return [("endpoint", {"id": "cameraSelectorMenuItem"})], ()
            return [], ()

        def _find_unique(self, identification, *, process_id=None):
            matches, trace = self._find_matches(identification, process_id=process_id)
            if not matches:
                raise LookupError("JavaFX component not found")
            if len(matches) > 1:
                raise LookupError("JavaFX component is ambiguous")
            endpoint, node = matches[0]
            return endpoint, node, trace

    install_javafx_transient_resolution(Driver)
    endpoint, node, _trace = Driver()._find_unique(LEGACY_CONTEXT_MENU_IDENTITY)
    assert endpoint == "endpoint"
    assert node["id"] == "cameraSelectorMenuItem"


def test_runtime_resolution_retries_until_transient_appears(monkeypatch):
    monkeypatch.setenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_TIMEOUT", "0.25")
    monkeypatch.setenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_RETRY_INTERVAL", "0.01")

    class Driver:
        _transient_resolution_policy_installed = False
        attempts = 0

        def _find_matches(self, identification, *, process_id=None):
            type(self).attempts += 1
            if type(self).attempts < 3:
                return [], ()
            return [("endpoint", {"id": "cameraSelectorMenuItem"})], ()

        def _find_unique(self, identification, *, process_id=None):
            matches, trace = self._find_matches(identification, process_id=process_id)
            if not matches:
                raise LookupError("JavaFX component not found")
            endpoint, node = matches[0]
            return endpoint, node, trace

    install_javafx_transient_resolution(Driver)
    endpoint, node, _trace = Driver()._find_unique({"mandatory": {"id": "cameraSelectorMenuItem"}})
    assert endpoint == "endpoint"
    assert node["id"] == "cameraSelectorMenuItem"
    assert Driver.attempts >= 3


def test_runtime_resolution_does_not_retry_ambiguity(monkeypatch):
    monkeypatch.setenv("AUTOMATION_HARNESS_OBJECT_RESOLUTION_TIMEOUT", "1")

    class Driver:
        _transient_resolution_policy_installed = False
        attempts = 0

        def _find_matches(self, identification, *, process_id=None):
            type(self).attempts += 1
            return [("endpoint", {"id": "one"}), ("endpoint", {"id": "two"})], ()

        def _find_unique(self, identification, *, process_id=None):
            matches, _trace = self._find_matches(identification, process_id=process_id)
            if len(matches) > 1:
                raise LookupError("JavaFX component is ambiguous")
            raise AssertionError("expected ambiguity")

    install_javafx_transient_resolution(Driver)
    with pytest.raises(LookupError, match="ambiguous"):
        Driver()._find_unique({"mandatory": {"accessible_role": "MENU_ITEM"}})
    assert Driver.attempts == 1
