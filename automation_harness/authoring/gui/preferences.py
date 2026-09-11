from __future__ import annotations

import json
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.preferences_runtime import AuthoringPreferences, preferences_path
from automation_harness.core.resolution_retry import object_resolution_timeout


def recording_highlights_enabled() -> bool:
    target = preferences_path()
    if not target.is_file():
        return True
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return bool(raw.get("recording_highlight_clicks", True)) if isinstance(raw, dict) else True


def show_preferences_dialog(owner, project=None):
    current = AuthoringPreferences.load()
    dialog = Gtk.Dialog(title="Preferences", transient_for=owner, modal=True)
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
    box = dialog.get_content_area(); box.set_spacing(10); box.set_border_width(12)
    grid = Gtk.Grid(column_spacing=8, row_spacing=8); box.pack_start(grid, True, True, 0)

    files = Gtk.Entry(); files.set_text(str(current.resolved_files_dir(project)))
    runs = Gtk.Entry(); runs.set_text(str(current.resolved_runs_dir(project)))
    highlight = Gtk.CheckButton(label="Highlight targets during recording")
    highlight.set_active(recording_highlights_enabled())
    resolution_timeout = Gtk.SpinButton.new_with_range(0.0, 120.0, 0.5)
    resolution_timeout.set_digits(1)
    resolution_timeout.set_value(object_resolution_timeout())
    resolution_timeout.set_tooltip_text(
        "How long object lookup retries before failing. Set 0 to disable retries."
    )

    def add_path_row(index, label_text, entry, title):
        label = Gtk.Label(label=label_text); label.set_xalign(0); grid.attach(label, 0, index, 1, 1)
        grid.attach(entry, 1, index, 1, 1)
        browse = Gtk.Button(label="Browse…"); grid.attach(browse, 2, index, 1, 1)
        def choose(*_args):
            chooser = Gtk.FileChooserDialog(title=title, transient_for=owner, action=Gtk.FileChooserAction.SELECT_FOLDER)
            chooser.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Select", Gtk.ResponseType.OK)
            candidate = Path(entry.get_text()).expanduser()
            if candidate.is_dir(): chooser.set_current_folder(str(candidate))
            response = chooser.run(); selected = chooser.get_filename() if response == Gtk.ResponseType.OK else None; chooser.destroy()
            if selected: entry.set_text(selected)
        browse.connect("clicked", choose)

    add_path_row(0, "Default files folder", files, "Select default files folder")
    add_path_row(1, "Test runs folder", runs, "Select test runs folder")
    timeout_label = Gtk.Label(label="Object resolution timeout (seconds)")
    timeout_label.set_xalign(0)
    grid.attach(timeout_label, 0, 2, 1, 1)
    grid.attach(resolution_timeout, 1, 2, 1, 1)
    grid.attach(highlight, 0, 3, 3, 1)
    dialog.show_all(); response = dialog.run()
    if response != Gtk.ResponseType.OK:
        dialog.destroy(); return False
    try:
        files_dir = Path(files.get_text().strip()).expanduser().resolve()
        runs_dir = Path(runs.get_text().strip()).expanduser().resolve()
        files_dir.mkdir(parents=True, exist_ok=True); runs_dir.mkdir(parents=True, exist_ok=True)
        AuthoringPreferences(files_dir, runs_dir).save()
        target = preferences_path()
        raw = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
        if not isinstance(raw, dict): raw = {}
        raw["recording_highlight_clicks"] = bool(highlight.get_active())
        raw["object_resolution_timeout"] = float(resolution_timeout.get_value())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        dialog.destroy()
    return True
