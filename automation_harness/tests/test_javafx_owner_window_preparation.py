"""Window preparation is independent of component identity for every JavaFX type.

The agent's read-only window inventory is authoritative for preparation;
component locators remain strict and immutable when the action is executed.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.drivers.javafx_bridge import (
    JavaFxBridgeDriver, JavaFxBridgeUnavailable, _explicit_window_hint,
)
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType


WINDOW = "ERSA Main Video Display"


class FakeEndpoint:
    def __init__(self, pid=1234, *, window=WINDOW, showing=True, fail_windows=False, fail_find=False):
        self.pid = pid
        self.port = 8888
        self.window = window
        self.showing = showing
        self.fail_windows = fail_windows
        self.fail_find = fail_find
        self.ready = False
        self.calls = []

    def request(self, operation, **kwargs):
        self.calls.append((operation, kwargs))
        if operation == "windows":
            if self.fail_windows:
                raise OSError("endpoint no longer available")
            return {"windows": [{"title": self.window, "showing": self.showing}]}
        if operation == "activate_window":
            # A target locator must never be needed to activate the Stage.
            assert kwargs.get("window") == self.window
            assert "identification" not in kwargs
            self.ready = True
            return {"window": self.window, "focused": True}
        if operation == "find":
            if self.fail_find:
                raise OSError("JavaFX endpoint disappeared during lookup")
            return {
                "matches": [{"id": "target", "window": self.window}] if self.ready else [],
                "stages": [{"source": "mandatory", "criteria": {}, "matches": int(self.ready)}],
            }
        if operation == "focus":
            assert self.ready
            return {"node": {"id": "target"}, "focused": True}
        raise AssertionError("unexpected operation " + operation)


def make_driver(monkeypatch, *endpoints):
    driver = JavaFxBridgeDriver()
    monkeypatch.setattr(driver, "endpoints", lambda: tuple(endpoints))
    return driver


@pytest.mark.parametrize(
    "object_type,action",
    [
        (ObjectType.MENU, ActionType.SELECT_MENU_ITEM),
        (ObjectType.BUTTON, ActionType.CLICK),
        (ObjectType.TOGGLE_BUTTON, ActionType.CLICK),
        (ObjectType.COMBO_BOX, ActionType.SELECT_ITEM),
        (ObjectType.TEXT_FIELD, ActionType.SET_TEXT),
        (ObjectType.PANEL, ActionType.CLICK),
        (ObjectType.CUSTOM, ActionType.CLICK),
    ],
)
def test_preparation_activates_owner_before_resolving_any_component_type(
    monkeypatch, object_type, action,
):
    endpoint = FakeEndpoint()
    driver = make_driver(monkeypatch, endpoint)
    monkeypatch.setattr(
        "automation_harness.core.component_handle.JavaFxBridgeDriver",
        lambda _context: driver,
    )
    events = []
    context = SimpleNamespace(
        backend="live-desktop",
        evidence=SimpleNamespace(record=lambda event, **payload: events.append((event, payload))),
    )
    definition = ComponentDefinition(
        "Target",
        object_type=object_type,
        strategies=(ComponentStrategy("javafx", {
            "identification": {
                "mandatory": {"id": "target"},
                "assistive": {"class": "javafx.scene.control.Control"},
            },
        }),),
        scope={"ownership": {"window": WINDOW}},
    )
    original_identity = definition.strategies[0].options["identification"]
    handle = ComponentHandle(context, definition)
    preparation = handle.prepare_for_interaction(action)
    assert preparation.successful
    assert preparation.window_activation.details["selection"] == "owning_window"
    assert endpoint.calls[0][0] == "windows"
    assert endpoint.calls[1][0] == "activate_window"
    # Focus-required actions resolve only *after* the Stage was prepared.
    if action == ActionType.SET_TEXT:
        assert [name for name, _ in endpoint.calls][-2:] == ["find", "focus"]
    # Target identification is still mandatory for the actual operation.
    assert driver.resolve("target", identification=original_identity).strategy == "javafx"
    assert definition.strategies[0].options["identification"] == original_identity
    assert any(name == "interaction_prepared" for name, _ in events)


def test_explicit_window_hint_from_locator_does_not_require_target_lookup(monkeypatch):
    endpoint = FakeEndpoint()
    driver = make_driver(monkeypatch, endpoint)
    identity = {"mandatory": {"id": "not-yet-constructed"},
                "assistive": {"window": WINDOW}}
    assert driver.activate_window(identification=identity)["focused"]
    assert [name for name, _ in endpoint.calls] == ["windows", "activate_window"]


def test_same_window_title_in_multiple_jvms_fails_closed(monkeypatch):
    driver = make_driver(monkeypatch, FakeEndpoint(1111), FakeEndpoint(2222))
    with pytest.raises(LookupError, match="ambiguous across endpoints"):
        driver.activate_window(window=WINDOW, identification={"mandatory": {"id": "target"}})
    assert all(not endpoint.ready for endpoint in driver.endpoints())


def test_process_id_scopes_exact_window_without_changing_identity(monkeypatch):
    first, second = FakeEndpoint(1111), FakeEndpoint(2222)
    driver = make_driver(monkeypatch, first, second)
    response = driver.activate_window(window=WINDOW, process_id=2222)
    assert response["bridge_pid"] == 2222
    assert not first.calls
    assert second.ready


def test_wrong_window_and_missing_window_do_not_guess_an_owner(monkeypatch):
    endpoint = FakeEndpoint(window="Unrelated")
    driver = make_driver(monkeypatch, endpoint)
    with pytest.raises(LookupError, match="not showing"):
        driver.activate_window(window=WINDOW)
    assert [name for name, _ in endpoint.calls] == ["windows"]


def test_hidden_window_is_not_activated(monkeypatch):
    endpoint = FakeEndpoint(showing=False)
    driver = make_driver(monkeypatch, endpoint)
    with pytest.raises(LookupError, match="not showing"):
        driver.activate_window(window=WINDOW)
    assert not endpoint.ready


def test_unresponsive_other_endpoint_prevents_unprovable_unique_selection(monkeypatch):
    good, stale = FakeEndpoint(1111), FakeEndpoint(2222, fail_windows=True)
    driver = make_driver(monkeypatch, good, stale)
    with pytest.raises(JavaFxBridgeUnavailable, match="did not respond"):
        driver.activate_window(window=WINDOW)
    assert not good.ready


def test_explicit_conflicting_window_evidence_is_rejected():
    with pytest.raises(ValueError, match="conflicting"):
        _explicit_window_hint(
            {"mandatory": {"id": "target"}, "assistive": {"window": "Other"}},
            window=WINDOW,
        )


def test_unscoped_legacy_target_does_not_choose_an_arbitrary_window(monkeypatch):
    endpoint = FakeEndpoint()
    driver = make_driver(monkeypatch, endpoint)
    with pytest.raises(LookupError, match="component not found"):
        driver.activate_window(identification={"mandatory": {"id": "missing"}})
    assert not endpoint.ready


def test_runtime_resolve_uses_persisted_window_scope_not_a_matching_foreign_id(monkeypatch):
    """The same control ID in a second JVM is not a valid alternative."""
    own = FakeEndpoint(1111)
    foreign = FakeEndpoint(2222, window="Unrelated")
    own.ready = foreign.ready = True
    driver = make_driver(monkeypatch, own, foreign)
    monkeypatch.setattr(
        "automation_harness.core.object_resolution.JavaFxBridgeDriver",
        lambda _context: driver,
    )
    context = SimpleNamespace(
        backend="live-desktop",
        evidence=SimpleNamespace(record=lambda *_args, **_kwargs: None),
    )
    definition = ComponentDefinition(
        "Identically Named Button",
        object_type=ObjectType.BUTTON,
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "target"}},
        }),),
        scope={"ownership": {"window": WINDOW}},
    )
    handle = ComponentHandle(context, definition)
    assert handle.resolve().strategy == "javafx"
    assert [operation for operation, _ in foreign.calls] == ["windows"]
    assert [operation for operation, _ in own.calls] == ["windows", "find"]
    assert "window" not in definition.strategies[0].options["identification"].get("assistive", {})


def test_conflicting_repository_scope_and_locator_rejects_resolution():
    definition = ComponentDefinition(
        "Conflicting",
        object_type=ObjectType.BUTTON,
        strategies=(ComponentStrategy("javafx", {
            "identification": {
                "mandatory": {"id": "target"},
                "assistive": {"window": "Another Window"},
            },
        }),),
        scope={"ownership": {"window": WINDOW}},
    )
    context = SimpleNamespace(backend="live-desktop")
    handle = ComponentHandle(context, definition)
    from automation_harness.core.component_handle import ComponentResolutionError
    with pytest.raises(ComponentResolutionError, match="conflicting"):
        handle._scoped_identification(
            "javafx", definition.strategies[0].options["identification"],
        )


def test_flat_legacy_identity_is_wrapped_without_modifying_its_mandatory_fields():
    definition = ComponentDefinition(
        "Old ID",
        object_type=ObjectType.BUTTON,
        scope={"ownership": {"window": WINDOW}},
    )
    legacy = {"id": "oldButton"}
    scoped = ComponentHandle(SimpleNamespace(), definition)._scoped_identification("javafx", legacy)
    assert scoped == {
        "mandatory": {"id": "oldButton"},
        "assistive": {"window": WINDOW},
    }
    assert legacy == {"id": "oldButton"}


def test_unscoped_locator_cannot_ignore_partial_endpoint_outages(monkeypatch):
    primary, unavailable = FakeEndpoint(1111), FakeEndpoint(2222, fail_find=True)
    primary.ready = unavailable.ready = True
    driver = make_driver(monkeypatch, primary, unavailable)
    with pytest.raises(JavaFxBridgeUnavailable, match="resolution incomplete"):
        driver.resolve("object", identification={"mandatory": {"id": "target"}})


def test_zero_match_error_includes_resolution_trace(monkeypatch):
    endpoint = FakeEndpoint()
    driver = make_driver(monkeypatch, endpoint)
    with pytest.raises(LookupError, match="resolution_stages"):
        driver.resolve("object", identification={"mandatory": {"id": "missing"}})
