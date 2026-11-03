"""Pure unit coverage for execution-time window orchestration and monitoring."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from automation_harness.core.component_handle import ComponentHandle
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_context import TestContext
from automation_harness.core.window_orchestration import (
    WindowMonitor, WindowOrchestrator, WindowOrchestrationError, WindowSnapshot,
)
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.gui import ActionType, ObjectType


class FakeMonitor:
    desktop_available = True

    def __init__(self):
        self.active = None
        self.calls = []

    def snapshot(self):
        return WindowSnapshot(0.0, self.active, self.active is not None)

    def activate(self, *, pid, title, timeout):
        self.calls.append((pid, title, timeout))
        self.active = 0xCAFE
        return {
            "native_window_id": "0xcafe",
            "active_window": "0xcafe",
            "native_foreground_verified": True,
        }


def test_monitor_observes_transitions_without_raising_or_changing_focus():
    state = iter([0x10, 0x10, 0x20])
    monitor = WindowMonitor(
        foreground_reader=lambda: next(state),
        desktop_available=lambda: True, history_size=2,
    )
    assert monitor.snapshot().active_native_id == 0x10
    transition = monitor.await_foreground_change(0x10, timeout=0.1)
    assert transition.active_native_id == 0x20
    assert tuple(item.active_native_id for item in monitor.history) == (0x10, 0x20)


def test_monitor_activation_requires_native_confirmation():
    monitor = WindowMonitor(
        desktop_available=lambda: True,
        native_activator=lambda **kwargs: {"native_foreground_verified": False},
        foreground_reader=lambda: 0x10,
    )
    with pytest.raises(RuntimeError, match="no foreground verification"):
        monitor.activate(pid=41, title="Main")


def test_orchestrator_verifies_exact_window_identity_after_driver_request():
    monitor = FakeMonitor()
    orchestrator = WindowOrchestrator(monitor)
    calls = []
    class Driver:
        def activate_window(self, **kwargs):
            calls.append(kwargs)
            return {"process_id": 451, "window": "Select Site", "focused": False}
    details = orchestrator.ensure_foreground(
        component_id="site.confirm", object_id="uuid-confirm",
        driver=Driver(), strategy="atspi",
        arguments={"identification": {"mandatory": {"id": "confirm"}}},
    )
    assert calls == [{"identification": {"mandatory": {"id": "confirm"}}}]
    assert monitor.calls == [(451, "Select Site", 2.0)]
    assert details["verification"] == "x11-ewmh"
    assert details["object_id"] == "uuid-confirm"
    assert details["native_window_id"] == "0xcafe"


def test_orchestrator_reuses_only_still_active_verified_foreground():
    monitor = FakeMonitor()
    orchestrator = WindowOrchestrator(monitor)
    invoked = []
    class Driver:
        def activate_window(self, **kwargs):
            invoked.append(True)
            return {"process_id": 41, "window": "Main"}
    args = dict(component_id="watch", object_id="uuid-watch",
                driver=Driver(), strategy="atspi", arguments={})
    orchestrator.ensure_foreground(**args)
    reused = orchestrator.ensure_foreground(**args)
    assert reused["reused_foreground"] is True
    assert len(invoked) == 1
    monitor.active = 0x123
    orchestrator.ensure_foreground(**args)
    assert len(invoked) == 2


def test_orchestrator_refuses_absent_or_failed_verification():
    monitor = FakeMonitor()
    monitor.desktop_available = False
    orchestrator = WindowOrchestrator(monitor)
    with pytest.raises(WindowOrchestrationError, match="no verifiable foreground evidence"):
        orchestrator.ensure_foreground(
            component_id="watch", object_id="uuid-watch",
            driver=SimpleNamespace(activate_window=lambda: {"window": "Main"}),
            strategy="java_agent", arguments={},
        )
    details = orchestrator.ensure_foreground(
        component_id="watch", object_id="uuid-watch",
        driver=SimpleNamespace(activate_window=lambda: {"focused": True, "window": "Main"}),
        strategy="javafx", arguments={},
    )
    assert details["verification"] == "backend-confirmed"


def test_orchestrator_does_not_accept_bridge_focus_over_native_mismatch():
    class BrokenMonitor(FakeMonitor):
        def activate(self, **kwargs):
            raise RuntimeError("wrong active X11 window")
    orchestrator = WindowOrchestrator(BrokenMonitor())
    with pytest.raises(WindowOrchestrationError, match="wrong active X11 window"):
        orchestrator.ensure_foreground(
            component_id="confirm", object_id="uuid-confirm",
            driver=SimpleNamespace(activate_window=lambda: {
                "focused": True, "window": "Select Site", "process_id": 88,
            }),
            strategy="javafx", arguments={},
        )


def test_test_context_prepares_only_mutating_gui_steps():
    actions = []
    handle = SimpleNamespace(prepare_for_interaction=lambda action, **kwargs: actions.append(
        (action, kwargs)
    ) or "prepared")
    context = SimpleNamespace(
        backend="live-desktop", component=lambda component_id: handle,
    )
    for step_name, expected in (
        ("gui.button.click", ActionType.CLICK),
        ("gui.text.set", ActionType.SET_TEXT),
        ("gui.selection.select", ActionType.SELECT_ITEM),
        ("gui.object.action", {"type": "close"}),
    ):
        inputs = {"component_id": "target", "action": {"type": "close"}}
        assert TestContext.prepare_gui_step(context, step_name, inputs) == "prepared"
        assert actions[-1][0] == expected
    assert TestContext.prepare_gui_step(context, "gui.object.state.assert", {
        "component_id": "target",
    }) is None


def test_canonical_visual_object_prepares_its_accessible_parent(monkeypatch):
    owner = ComponentDefinition(
        "Parent Stage", object_id="aaaabbbb-0000-4000-8000-000000000001", object_type=ObjectType.WINDOW,
        strategies=(ComponentStrategy("javafx", {
            "identification": {"mandatory": {"id": "window"}},
        }),),
        scope={"ownership": {"window": "Main Display"}},
    )
    visual = ComponentDefinition(
        "Rendered Watch", object_id="aaaabbbb-0000-4000-8000-000000000002",
        owner_object_id=owner.object_id, object_type=ObjectType.CUSTOM,
        actions=frozenset({"click"}),
        strategies=(ComponentStrategy("anchored_visual", {
            "relative_bounds": [0.1, 0.1, 0.2, 0.2],
        }),),
    )
    repo = ComponentRepository({owner.component_id: owner, visual.component_id: visual})
    observed = []
    class Fx:
        def __init__(self, _ctx):
            pass
        def activate_window(self, **kwargs):
            observed.append(kwargs)
            return {"window": "Main Display", "focused": True}
    monkeypatch.setattr("automation_harness.core.component_handle.JavaFxBridgeDriver", Fx)
    events = SimpleNamespace(record=lambda *_a, **_kw: None)
    handle = ComponentHandle(
        SimpleNamespace(backend="live-desktop", evidence=events, components=repo), visual,
    )
    preparation = handle.prepare_for_interaction(ActionType.CLICK)
    assert preparation.successful
    assert preparation.strategy == "visual-owner:javafx"
    assert observed[0]["window"] == "Main Display"


def test_unowned_visual_object_fails_before_pointer_action():
    visual = ComponentDefinition(
        "Unowned Widget", object_type=ObjectType.CUSTOM,
        actions=frozenset({"click"}),
        strategies=(ComponentStrategy("anchored_visual", {
            "relative_bounds": [0.2, 0.2, 0.2, 0.2],
        }),),
    )
    events = SimpleNamespace(record=lambda *_a, **_kw: None)
    handle = ComponentHandle(
        SimpleNamespace(backend="live-desktop", evidence=events), visual,
    )
    with pytest.raises(RuntimeError, match="no canonical accessibility owner"):
        handle.prepare_for_interaction(ActionType.CLICK)

def test_window_inventory_tracks_open_close_and_title_change():
    windows = [
        {"native_window_id": "0x10", "process_id": 11, "title": "Main"},
    ]
    monitor = WindowMonitor(
        desktop_available=lambda: True,
        foreground_reader=lambda: 0x10,
        window_lister=lambda: tuple(windows),
    )
    assert monitor.inventory()[0].title == "Main"
    assert not monitor.lifecycle
    windows.append({"native_window_id": "0x20", "process_id": 11, "title": "Dialog"})
    assert len(monitor.inventory()) == 2
    assert monitor.lifecycle[-1].kind == "opened"
    windows[0] = {"native_window_id": "0x10", "process_id": 11, "title": "Renamed"}
    monitor.inventory()
    assert monitor.lifecycle[-1].kind == "changed"
    windows.pop()
    monitor.inventory()
    assert monitor.lifecycle[-1].kind == "closed"


def test_await_window_refuses_ambiguous_title_instead_of_guessing():
    monitor = WindowMonitor(
        desktop_available=lambda: True,
        window_lister=lambda: (
            {"native_window_id": "0x10", "process_id": 11, "title": "Main"},
            {"native_window_id": "0x20", "process_id": 22, "title": "Main"},
        ),
    )
    orchestrator = WindowOrchestrator(monitor)
    with pytest.raises(WindowOrchestrationError, match="ambiguous"):
        orchestrator.await_window(title="Main", timeout=0)
    selected = orchestrator.await_window(title="Main", process_id=22, timeout=0)
    assert selected.native_window_id == 0x20

def test_orchestrator_rejects_driver_activating_wrong_owner():
    monitor = FakeMonitor()
    monitor.desktop_available = False
    orchestrator = WindowOrchestrator(monitor)
    with pytest.raises(WindowOrchestrationError, match="instead of owning window"):
        orchestrator.ensure_foreground(
            component_id="button", object_id="uuid-button",
            driver=SimpleNamespace(activate_window=lambda **kwargs: {
                "window": "Unrelated", "focused": True,
            }),
            strategy="javafx",
            arguments={"window": "Expected Dialog"},
        )
