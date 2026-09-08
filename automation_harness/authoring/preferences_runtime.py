"""User preferences and run-time window policy for live authoring.

This module is installed by the Python-version-safe authoring entry point.  It
keeps user-level path preferences outside project artifacts and ensures harness
windows do not occlude the application under test while a run is executing.
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.test_plan import validate_plan, validate_plan_components
from automation_harness.formats import with_artifact_suffix
from automation_harness.runner.plan_execution import execute_plan


_PREFERENCES_VERSION = 1
_RUN_SETTLE_DELAY_MS = 350


def preferences_path():
    root = os.environ.get("XDG_CONFIG_HOME")
    if root:
        base = Path(root).expanduser()
    else:
        base = Path.home() / ".config"
    return base / "automation-harness" / "preferences.json"


@dataclass(frozen=True)
class AuthoringPreferences:
    default_files_dir: object = None
    runs_dir: object = None

    @classmethod
    def load(cls, path=None):
        target = Path(path) if path is not None else preferences_path()
        if not target.is_file():
            return cls()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(
            default_files_dir=_optional_directory(raw.get("default_files_dir")),
            runs_dir=_optional_directory(raw.get("runs_dir")),
        )

    def save(self, path=None):
        target = Path(path) if path is not None else preferences_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _PREFERENCES_VERSION,
            "default_files_dir": _path_text(self.default_files_dir),
            "runs_dir": _path_text(self.runs_dir),
        }
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
        return target

    def resolved_files_dir(self, project=None):
        if self.default_files_dir is not None:
            return Path(self.default_files_dir).expanduser().resolve()
        if project is not None:
            return Path(project.root).resolve()
        return Path.cwd().resolve()

    def resolved_runs_dir(self, project=None):
        if self.runs_dir is not None:
            return Path(self.runs_dir).expanduser().resolve()
        if project is not None:
            return Path(project.runs_dir).resolve()
        return (Path.cwd() / "runs").resolve()


def _optional_directory(value):
    if not isinstance(value, str) or not value.strip():
        return None
    return Path(value).expanduser()


def _path_text(value):
    return str(Path(value).expanduser()) if value is not None else None


def _preferences(app_instance):
    value = getattr(app_instance, "preferences", None)
    if isinstance(value, AuthoringPreferences):
        return value
    value = AuthoringPreferences.load()
    app_instance.preferences = value
    return value


def _is_harness_window(app_instance, window):
    if window is getattr(app_instance, "window", None):
        return True
    if window is getattr(app_instance, "recording_stop_window", None):
        return True
    if window in tuple(getattr(app_instance, "_highlight_windows", ()) or ()):
        return True
    try:
        title = window.get_title() or ""
    except Exception:
        title = ""
    return title.startswith("Automation Harness")


def _suspend_harness_windows(app_instance, Gtk, Gdk):
    """Remove harness-owned windows from the framebuffer before execution."""
    restore = []
    overlays = set(getattr(app_instance, "_highlight_windows", ()) or ())
    for window in tuple(Gtk.Window.list_toplevels()):
        if not _is_harness_window(app_instance, window):
            continue
        try:
            if not window.get_visible():
                continue
        except Exception:
            continue
        if window in overlays:
            # Highlight edges are temporary evidence helpers, not user windows;
            # they must disappear and should not be restored after a run.
            try:
                window.hide()
            except Exception:
                pass
            continue
        try:
            window.iconify()
            restore.append(window)
        except Exception:
            try:
                window.hide()
                restore.append(window)
            except Exception:
                pass
    try:
        Gdk.flush()
    except Exception:
        pass
    app_instance._run_minimized_windows = restore
    return tuple(restore)


def _restore_harness_windows(app_instance):
    windows = tuple(getattr(app_instance, "_run_minimized_windows", ()) or ())
    app_instance._run_minimized_windows = []
    for window in windows:
        try:
            window.show()
            window.deiconify()
        except Exception:
            pass
    primary = getattr(app_instance, "window", None)
    if primary is not None:
        try:
            primary.present()
        except Exception:
            pass


def install(app_module):
    """Install preferences and framebuffer-safe Run Test behavior."""
    if getattr(app_module, "_authoring_preferences_runtime_installed", False):
        return

    Gtk = app_module.Gtk
    Gdk = app_module.Gdk
    GLib = app_module.GLib
    original_init = app_module.AuthoringApp.__init__
    original_build = app_module.AuthoringApp._build
    original_present_result = app_module.AuthoringApp._present_reference_result
    original_present_error = app_module.AuthoringApp._present_run_error

    def init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.preferences = AuthoringPreferences.load()
        self._run_minimized_windows = []

    def build(self):
        original_build(self)
        outer = self.window.get_child()
        children = outer.get_children() if outer is not None else []
        if children:
            toolbar = children[0]
            self._button(toolbar, "Preferences", self.preferences_dialog)

    def choose_directory(self, title, initial):
        dialog = Gtk.FileChooserDialog(
            title=title,
            transient_for=self.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Select", Gtk.ResponseType.OK)
        initial = Path(initial)
        if initial.is_dir():
            dialog.set_current_folder(str(initial))
        response = dialog.run()
        value = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        return value

    def preferences_dialog(self):
        current = _preferences(self)
        dialog = Gtk.Dialog(title="Preferences", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(12)
        grid = Gtk.Grid(column_spacing=8, row_spacing=8)
        box.pack_start(grid, True, True, 0)

        files_entry = Gtk.Entry()
        files_entry.set_text(str(current.resolved_files_dir(self.project)))
        runs_entry = Gtk.Entry()
        runs_entry.set_text(str(current.resolved_runs_dir(self.project)))

        def row(index, label_text, entry, title):
            label = Gtk.Label(label=label_text)
            label.set_xalign(0)
            grid.attach(label, 0, index, 1, 1)
            grid.attach(entry, 1, index, 1, 1)
            browse = Gtk.Button(label="Browse…")

            def browse_clicked(*_args):
                selected = self._choose_preferences_directory(title, entry.get_text())
                if selected:
                    entry.set_text(selected)

            browse.connect("clicked", browse_clicked)
            grid.attach(browse, 2, index, 1, 1)

        row(0, "Default files folder", files_entry, "Select default files folder")
        row(1, "Test runs folder", runs_entry, "Select test runs folder")
        note = Gtk.Label(
            label="These are user preferences. Existing project files are not rewritten."
        )
        note.set_xalign(0)
        grid.attach(note, 0, 2, 3, 1)

        dialog.show_all()
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return
        try:
            files_dir = Path(files_entry.get_text().strip()).expanduser().resolve()
            runs_dir = Path(runs_entry.get_text().strip()).expanduser().resolve()
            files_dir.mkdir(parents=True, exist_ok=True)
            runs_dir.mkdir(parents=True, exist_ok=True)
            preferences = AuthoringPreferences(files_dir, runs_dir)
            saved_to = preferences.save()
        except Exception as exc:
            dialog.destroy()
            return self._error("Preferences", "%s: %s" % (type(exc).__name__, exc))
        dialog.destroy()
        self.preferences = preferences
        self._set_status("Saved preferences: %s" % saved_to)

    def choose_file(self, save=False, yaml=False, artifact_suffix=None, title="Select file"):
        action = Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN
        dialog = Gtk.FileChooserDialog(title=title, transient_for=self.window, action=action)
        dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Save" if save else "Open", Gtk.ResponseType.OK,
        )
        if save:
            dialog.set_do_overwrite_confirmation(True)
        preferred = _preferences(self).resolved_files_dir(self.project)
        if preferred.is_dir():
            dialog.set_current_folder(str(preferred))
        if yaml:
            filt = Gtk.FileFilter()
            filt.set_name("Automation Harness YAML")
            if artifact_suffix:
                filt.add_pattern("*" + artifact_suffix)
            filt.add_pattern("*.yaml")
            filt.add_pattern("*.yml")
            dialog.add_filter(filt)
        if save and artifact_suffix:
            dialog.set_current_name("untitled" + artifact_suffix)
        response = dialog.run()
        filename = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if filename and save and artifact_suffix:
            filename = str(with_artifact_suffix(Path(filename), artifact_suffix))
        return filename

    def run_reference_plan(self):
        if self._run_active:
            return
        self.refresh_plan()
        issues = validate_plan(self.plan, self.registry)
        issues.extend(validate_plan_components(self.plan, self.repository))
        if issues:
            return self._error("Plan validation", "\n".join(issues))

        try:
            runs_dir = _preferences(self).resolved_runs_dir(self.project)
            runs_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            return self._error("Test runs folder", "%s: %s" % (type(exc).__name__, exc))

        self._run_active = True
        self.run_reference_button.set_sensitive(False)
        self._set_status("Preparing desktop and minimizing Automation Harness windows…")
        plan = self.plan
        repository = self.repository
        _suspend_harness_windows(self, Gtk, Gdk)

        def start_worker():
            self._set_status("Running test against the current desktop environment…")

            def worker():
                try:
                    result = execute_plan(
                        plan,
                        LiveDesktopBackend(),
                        runs_dir=runs_dir,
                        component_repository=repository,
                    )
                except Exception as exc:
                    GLib.idle_add(self._present_run_error, exc)
                    return
                GLib.idle_add(self._present_reference_result, result)

            threading.Thread(
                target=worker,
                name="automation-author-run",
                daemon=True,
            ).start()
            return False

        GLib.timeout_add(_RUN_SETTLE_DELAY_MS, start_worker)

    def present_result(self, result):
        _restore_harness_windows(self)
        return original_present_result(self, result)

    def present_error(self, error):
        _restore_harness_windows(self)
        return original_present_error(self, error)

    app_module.AuthoringApp.__init__ = init
    app_module.AuthoringApp._build = build
    app_module.AuthoringApp._choose_preferences_directory = choose_directory
    app_module.AuthoringApp.preferences_dialog = preferences_dialog
    app_module.AuthoringApp._choose_file = choose_file
    app_module.AuthoringApp.run_reference_plan = run_reference_plan
    app_module.AuthoringApp._present_reference_result = present_result
    app_module.AuthoringApp._present_run_error = present_error
    app_module._authoring_preferences_runtime_installed = True
