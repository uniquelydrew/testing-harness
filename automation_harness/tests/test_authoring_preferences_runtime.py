from pathlib import Path
from types import SimpleNamespace

from automation_harness.authoring.preferences_runtime import (
    AuthoringPreferences,
    _restore_harness_windows,
    _suspend_harness_windows,
)


class _Window:
    def __init__(self, title, visible=True):
        self.title = title
        self.visible = visible
        self.iconified = False
        self.hidden = False
        self.shown = False
        self.deiconified = False
        self.presented = False

    def get_title(self):
        return self.title

    def get_visible(self):
        return self.visible

    def iconify(self):
        self.iconified = True

    def hide(self):
        self.hidden = True
        self.visible = False

    def show(self):
        self.shown = True
        self.visible = True

    def deiconify(self):
        self.deiconified = True
        self.iconified = False

    def present(self):
        self.presented = True


class _WindowType:
    windows = []

    @classmethod
    def list_toplevels(cls):
        return list(cls.windows)


class _Gtk:
    Window = _WindowType


class _Gdk:
    flushed = False

    @classmethod
    def flush(cls):
        cls.flushed = True


def test_preferences_round_trip(tmp_path):
    config = tmp_path / "config" / "preferences.json"
    files = tmp_path / "authored"
    runs = tmp_path / "results"

    saved = AuthoringPreferences(files, runs)
    assert saved.save(config) == config

    loaded = AuthoringPreferences.load(config)
    assert loaded.default_files_dir == files
    assert loaded.runs_dir == runs
    assert loaded.resolved_files_dir() == files.resolve()
    assert loaded.resolved_runs_dir() == runs.resolve()


def test_preferences_without_user_override_preserve_project_defaults(tmp_path):
    project = SimpleNamespace(root=tmp_path / "project", runs_dir=tmp_path / "project" / "runs")
    preferences = AuthoringPreferences()

    assert preferences.resolved_files_dir(project) == project.root.resolve()
    assert preferences.resolved_runs_dir(project) == project.runs_dir.resolve()


def test_run_suspension_minimizes_harness_windows_and_discards_highlight_overlay():
    primary = _Window("Automation Harness Author")
    auxiliary = _Window("Automation Harness Object Repository")
    overlay = _Window("")
    unrelated = _Window("Application Under Test")
    _WindowType.windows = [primary, auxiliary, overlay, unrelated]
    _Gdk.flushed = False

    app = SimpleNamespace(
        window=primary,
        recording_stop_window=None,
        _highlight_windows=[overlay],
        _run_minimized_windows=[],
    )

    suspended = _suspend_harness_windows(app, _Gtk, _Gdk)

    assert suspended == (primary, auxiliary)
    assert primary.iconified is True
    assert auxiliary.iconified is True
    assert overlay.hidden is True
    assert unrelated.iconified is False
    assert _Gdk.flushed is True

    _restore_harness_windows(app)

    assert primary.deiconified is True
    assert auxiliary.deiconified is True
    assert primary.presented is True
    assert overlay.shown is False
