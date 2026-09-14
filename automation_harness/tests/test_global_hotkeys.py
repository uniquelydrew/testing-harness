from __future__ import annotations

import pytest

from automation_harness.authoring.global_hotkeys import (
    GlobalHotkeyService,
    HotkeyBinding,
    HotkeyConflictError,
    bindings_from_environment,
    global_hotkeys_enabled,
    parse_accelerator,
)


class _FakeBackend:
    def __init__(self, conflicts=()):
        self.conflicts = set(conflicts)
        self.registered = []
        self.callback = None
        self.stopped = False

    def register(self, action, accelerator):
        if action in self.conflicts:
            raise HotkeyConflictError("reserved")
        self.registered.append((action, accelerator))

    def start(self, callback):
        self.callback = callback

    def emit(self, action):
        self.callback(action)

    def stop(self):
        self.stopped = True


def _immediate(callback, *args):
    return callback(*args)


def test_parse_accelerator_normalizes_modifiers_and_single_character_key():
    parsed = parse_accelerator("Ctrl+Alt+C")
    assert parsed.key == "c"
    assert parsed.modifiers == (1 << 2) | (1 << 3)


def test_parse_accelerator_rejects_unknown_modifier():
    with pytest.raises(ValueError, match="unsupported hotkey modifier"):
        parse_accelerator("Hyper+C")


def test_bindings_can_be_overridden_or_disabled_from_environment():
    bindings = bindings_from_environment({
        "AUTOMATION_HARNESS_HOTKEY_CAPTURE_POINTER": "Super+F8",
        "AUTOMATION_HARNESS_HOTKEY_CAPTURE_NEXT_CLICK": "off",
    })
    values = {item.action: item.accelerator for item in bindings}
    assert values["capture_pointer"] == "Super+F8"
    assert "capture_next_click" not in values
    assert values["start_recording"] == "Ctrl+Alt+R"
    assert values["transient_capture"] == "Ctrl+Alt+T"


def test_global_hotkeys_can_be_disabled_as_a_group():
    assert global_hotkeys_enabled({"AUTOMATION_HARNESS_GLOBAL_HOTKEYS": "0"}) is False
    assert global_hotkeys_enabled({}) is True


def test_service_delivers_registered_action_through_scheduler():
    backend = _FakeBackend()
    delivered = []
    service = GlobalHotkeyService(
        delivered.append,
        backend=backend,
        bindings=(HotkeyBinding("capture_pointer", "Ctrl+Alt+C"),),
        scheduler=_immediate,
    )
    status = service.start()
    assert status == {"registered": {"capture_pointer": "Ctrl+Alt+C"}, "errors": {}}
    backend.emit("capture_pointer")
    assert delivered == ["capture_pointer"]
    service.stop()
    assert backend.stopped is True


def test_service_keeps_nonconflicting_bindings_when_one_is_reserved():
    backend = _FakeBackend(conflicts={"start_recording"})
    service = GlobalHotkeyService(
        lambda _action: None,
        backend=backend,
        bindings=(
            HotkeyBinding("capture_pointer", "Ctrl+Alt+C"),
            HotkeyBinding("start_recording", "Ctrl+Alt+R"),
        ),
        scheduler=_immediate,
    )
    status = service.start()
    assert status["registered"] == {"capture_pointer": "Ctrl+Alt+C"}
    assert "start_recording" in status["errors"]
    assert backend.callback is not None
