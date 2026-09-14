from __future__ import annotations

import argparse
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.global_hotkeys import create_global_hotkey_service
from automation_harness.authoring.gui.router import open_window
from automation_harness.authoring.gui.start_window import StartWindow


_OPEN_WINDOWS = []
_LAST_ACTIVE_WINDOW = None
_HOTKEY_SERVICE = None

_HOTKEY_METHODS = {
    "capture_pointer": ("_capture_pointer_now",),
    "capture_next_click": ("capture_next_click",),
    "start_recording": ("start_recording",),
    "stop_recording": ("stop_recording",),
    # Transient capture is intentionally a point-in-time pointer capture. The
    # synthetic click that opened the menu/popup already happened; this command
    # must inspect the exposed target without first taking focus from it.
    "transient_capture": ("_capture_pointer_now",),
}


def _track(window):
    global _LAST_ACTIVE_WINDOW
    if window in _OPEN_WINDOWS:
        return window
    _OPEN_WINDOWS.append(window)
    _LAST_ACTIVE_WINDOW = window
    window.window.connect("destroy", lambda *_args: _window_closed(window))
    window.window.connect("focus-in-event", lambda *_args: _window_focused(window))
    _ensure_hotkeys()
    return window


def _window_focused(window):
    global _LAST_ACTIVE_WINDOW
    if window in _OPEN_WINDOWS:
        _LAST_ACTIVE_WINDOW = window
    return False


def _window_closed(window):
    global _LAST_ACTIVE_WINDOW, _HOTKEY_SERVICE
    try:
        _OPEN_WINDOWS.remove(window)
    except ValueError:
        pass
    if _LAST_ACTIVE_WINDOW is window:
        _LAST_ACTIVE_WINDOW = _OPEN_WINDOWS[-1] if _OPEN_WINDOWS else None
    if not _OPEN_WINDOWS:
        if _HOTKEY_SERVICE is not None:
            _HOTKEY_SERVICE.stop()
            _HOTKEY_SERVICE = None
        Gtk.main_quit()


def _hotkey_method(window, action):
    for method_name in _HOTKEY_METHODS.get(action, ()):
        method = getattr(window, method_name, None)
        if callable(method):
            return method
    return None


def _set_window_status(window, message):
    setter = getattr(window, "set_status", None)
    if callable(setter):
        try:
            setter(message)
        except Exception:
            pass


def _dispatch_hotkey(action):
    """Dispatch only to the most recently focused Automation Harness window.

    This is deliberately not a repository-wide capability search. If the user
    last focused a Project window, a recording hotkey must not unexpectedly
    start some other open Test Plan. When focus moves to the application under
    test, the last focused authoring window remains the command owner.
    """
    window = _LAST_ACTIVE_WINDOW
    if window is None or window not in _OPEN_WINDOWS:
        return False
    method = _hotkey_method(window, action)
    if method is None:
        _set_window_status(window, "Hotkey %s is not available in this window" % action.replace("_", " "))
        return False
    try:
        method()
    except Exception as exc:
        _set_window_status(window, "Hotkey %s failed: %s" % (action.replace("_", " "), exc))
    return False


def _ensure_hotkeys():
    global _HOTKEY_SERVICE
    if _HOTKEY_SERVICE is not None:
        return _HOTKEY_SERVICE
    try:
        service = create_global_hotkey_service(_dispatch_hotkey)
        if service is None:
            return None
        status = service.start()
    except Exception as exc:
        window = _LAST_ACTIVE_WINDOW
        if window is not None:
            _set_window_status(window, "Global hotkeys unavailable: %s" % exc)
        return None
    _HOTKEY_SERVICE = service
    if status["errors"] and _LAST_ACTIVE_WINDOW is not None:
        failed = ", ".join(sorted(status["errors"]))
        _set_window_status(_LAST_ACTIVE_WINDOW, "Global hotkeys active; unavailable bindings: %s" % failed)
    return service


def open_tracked(path, *, project_context=None, launching_window=None):
    return _track(
        open_window(
            Path(path),
            project_context=project_context,
            launching_window=launching_window,
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="automation-author", description="Artifact-aware Automation Harness authoring GUI")
    parser.add_argument("artifact", nargs="?", type=Path, help=".ahproject, .ahplan, .ahregistry, or .ahobjects")
    parser.add_argument("--project", type=Path, help="legacy alias for opening a Project")
    parser.add_argument("--repository", type=Path, help="legacy alias for opening an Object Repository")
    parser.add_argument("--smoke-test", action="store_true", help="construct the selected GUI once, then exit")
    args = parser.parse_args(argv)

    selected = args.artifact or args.project or args.repository
    try:
        if selected is None:
            window = StartWindow(opener=open_tracked).finish_build()
            _track(window)
        else:
            window = open_tracked(selected)
    except Exception as exc:
        dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.OK, text="Unable to open artifact")
        dialog.format_secondary_text("%s: %s" % (type(exc).__name__, exc)); dialog.run(); dialog.destroy(); return 2

    if args.smoke_test:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        for item in tuple(_OPEN_WINDOWS):
            item.window.destroy()
        return 0
    Gtk.main(); return 0
