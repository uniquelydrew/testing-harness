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
            return {"process_id": 451, "window": "Select Workspace", "focused": False}
    details = orchestrator.ensure_foreground(
        component_id="site.confirm", object_id="uuid-confirm",
        driver=Driver(), strategy="atspi",
        arguments={"identification": {"mandatory": {"id": "confirm"}}},
    )
    assert calls == [{"identification": {"mandatory": {"id": "confirm"}}}]
    assert monitor.calls == [(451, "Select Workspace", 2.0)]
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
    args = {"component_id": "watch", "object_id": "uuid-watch",
            "driver": Driver(), "strategy": "atspi", "arguments": {}}
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
                "focused": True, "window": "Select Workspace", "process_id": 88,
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


def test_javafx_stage_focus_is_authoritative_when_x11_window_is_unlisted():
    """X11 can omit _NET_WM_PID or expose a different WM_NAME.

    The JavaFX bridge has already resolved the exact Stage in one JVM,
    and waited for Stage.isFocused(); unmatched EWMH metadata must not
    turn that verified focus into a false negative.
    """
    from automation_harness.drivers.x11_foreground import X11WindowNotFound

    class UnlistedMonitor(FakeMonitor):
        def activate(self, **kwargs):
            self.calls.append(kwargs)
            raise X11WindowNotFound("no X11 window matched")

    monitor = UnlistedMonitor()
    orchestrator = WindowOrchestrator(monitor)
    details = orchestrator.ensure_foreground(
        component_id="File Menu",
        object_id="uuid-file-menu",
        strategy="javafx",
        driver=SimpleNamespace(activate_window=lambda **kwargs: {
            "window": "SampleApp build.0 Main Video Display, SITE.op1, domain 12",
            "bridge_pid": 5215,
            "selection": "owning_window",
            "focused": True,
        }),
        arguments={"window": "SampleApp build.0 Main Video Display, SITE.op1, domain 12"},
    )
    assert details["verification"] == "javafx-stage-focused"
    assert details["native_foreground_verified"] is False
    assert "X11WindowNotFound" in details["native_lookup_diagnostic"]
    assert details["window"].startswith("SampleApp test.build.0")
    assert len(monitor.calls) == 1


@pytest.mark.parametrize("strategy,selection,focused", [
    ("atspi", "owning_window", True),
    ("java_agent", "owning_window", True),
    ("javafx", "resolved_component", True),
    ("javafx", "owning_window", False),
])
def test_missing_x11_window_fallback_requires_unique_focused_javafx_stage(
    strategy, selection, focused,
):
    from automation_harness.drivers.x11_foreground import X11WindowNotFound

    class UnlistedMonitor(FakeMonitor):
        def activate(self, **kwargs):
            raise X11WindowNotFound("no native owner")

    orchestrator = WindowOrchestrator(UnlistedMonitor())
    with pytest.raises(WindowOrchestrationError, match="X11WindowNotFound"):
        orchestrator.ensure_foreground(
            component_id="File Menu", object_id="uuid-file-menu",
            strategy=strategy,
            driver=SimpleNamespace(activate_window=lambda **kwargs: {
                "bridge_pid": 5215, "window": "SampleApp Main",
                "selection": selection, "focused": focused,
            }),
            arguments={"window": "SampleApp Main"},
        )


def test_native_foreground_timeout_must_not_fallback_to_javafx_claim():
    class TimeoutMonitor(FakeMonitor):
        def activate(self, **kwargs):
            raise TimeoutError("native foreground window is different")

    orchestrator = WindowOrchestrator(TimeoutMonitor())
    with pytest.raises(WindowOrchestrationError, match="native foreground window is different"):
        orchestrator.ensure_foreground(
            component_id="File Menu", object_id="uuid-file-menu",
            strategy="javafx",
            driver=SimpleNamespace(activate_window=lambda **kwargs: {
                "bridge_pid": 5215, "window": "SampleApp Main",
                "selection": "owning_window", "focused": True,
            }),
            arguments={"window": "SampleApp Main"},
        )


def test_javafx_native_handoff_precedes_stage_focus_verification_with_terminal_in_front():
    """An unrelated active terminal must not starve the native focus handoff."""
    order = []
    state = {"focused": False, "active": 0x777}

    class ForegroundMonitor:
        desktop_available = True

        def snapshot(self):
            return WindowSnapshot(0.0, state["active"], True)

        def activate(self, *, pid, title, timeout):
            order.append(("x11.activate", pid, title))
            assert state["focused"] is False
            assert state["active"] == 0x777
            state["active"] = 0x111
            state["focused"] = True
            return {
                "native_window_id": "0x111",
                "native_foreground_verified": True,
                "active_window": "0x111",
                "previous_active_window": "0x777",
            }

    class JavaFx:
        def request_window_activation(self, **kwargs):
            order.append(("javafx.request", kwargs.get("window")))
            return {
                "bridge_pid": 5215, "window": "SampleApp Main",
                "selection": "owning_window", "focused": False,
                "activation_attempts": 1,
            }

        def activate_window(self, **kwargs):
            order.append(("javafx.verify", kwargs.get("window")))
            assert state["focused"]
            return {
                "bridge_pid": 5215, "window": "SampleApp Main", "focused": True,
                "activation_attempts": 1,
            }

    result = WindowOrchestrator(ForegroundMonitor()).ensure_foreground(
        component_id="File Menu", object_id="uuid-file",
        strategy="javafx", driver=JavaFx(), arguments={"window": "SampleApp Main"},
    )
    assert [entry[0] for entry in order] == [
        "javafx.request", "x11.activate", "javafx.verify",
    ]
    assert result["verification"] == "x11-ewmh"
    assert result["focused"] is True
    assert result["previous_active_window"] == "0x777"


def test_failed_native_handoff_does_not_attempt_semantic_action_or_focus_poll():
    calls = []

    class ForegroundMonitor:
        desktop_available = True

        def activate(self, **kwargs):
            calls.append("native")
            raise RuntimeError("terminal remains foreground")

    class JavaFx:
        def request_window_activation(self, **kwargs):
            calls.append("request")
            return {
                "bridge_pid": 5215, "window": "SampleApp Main",
                "selection": "owning_window", "focused": False,
            }

        def activate_window(self, **kwargs):
            calls.append("verify")
            return {"bridge_pid": 5215, "window": "SampleApp Main", "focused": True}

    with pytest.raises(WindowOrchestrationError, match="terminal remains foreground"):
        WindowOrchestrator(ForegroundMonitor()).ensure_foreground(
            component_id="File Menu", object_id="uuid-file",
            strategy="javafx", driver=JavaFx(), arguments={"window": "SampleApp Main"},
        )
    assert calls == ["request", "native"]


def test_unlisted_x11_window_does_not_grant_unfocused_stage_access():
    from automation_harness.drivers.x11_foreground import X11WindowNotFound

    class ForegroundMonitor:
        desktop_available = True

        def activate(self, **kwargs):
            raise X11WindowNotFound("X11 title mismatch")

    class JavaFx:
        def request_window_activation(self, **kwargs):
            return {
                "bridge_pid": 5215, "window": "SampleApp Main",
                "selection": "owning_window", "focused": False,
            }

        def activate_window(self, **kwargs):
            raise RuntimeError("JavaFX Stage did not confirm foreground focus")

    with pytest.raises(WindowOrchestrationError, match="native X11 owner is unavailable"):
        WindowOrchestrator(ForegroundMonitor()).ensure_foreground(
            component_id="File Menu", object_id="uuid-file",
            strategy="javafx", driver=JavaFx(), arguments={"window": "SampleApp Main"},
        )


def test_handoff_rejects_javafx_stage_identity_changes_after_native_activation():
    class ForegroundMonitor(FakeMonitor):
        def activate(self, **kwargs):
            return {
                "native_window_id": "0x111", "native_foreground_verified": True,
            }

    class JavaFx:
        def request_window_activation(self, **kwargs):
            return {
                "bridge_pid": 5215, "window": "SampleApp Main",
                "selection": "owning_window", "focused": False,
            }

        def activate_window(self, **kwargs):
            return {"bridge_pid": 5216, "window": "SampleApp Main", "focused": True}

    with pytest.raises(WindowOrchestrationError, match="identity changed"):
        WindowOrchestrator(ForegroundMonitor()).ensure_foreground(
            component_id="File Menu", object_id="uuid-file",
            strategy="javafx", driver=JavaFx(), arguments={"window": "SampleApp Main"},
        )
