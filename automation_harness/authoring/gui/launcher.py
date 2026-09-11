from __future__ import annotations

import argparse
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.gui.router import open_window
from automation_harness.authoring.gui.start_window import StartWindow


_OPEN_WINDOWS = []


def _track(window):
    if window in _OPEN_WINDOWS:
        return window
    _OPEN_WINDOWS.append(window)
    window.window.connect("destroy", lambda *_args: _window_closed(window))
    return window


def _window_closed(window):
    try:
        _OPEN_WINDOWS.remove(window)
    except ValueError:
        pass
    if not _OPEN_WINDOWS:
        Gtk.main_quit()


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
