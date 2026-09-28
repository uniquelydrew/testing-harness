"""Project-first artifact launcher window."""
from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.gui.common import ArtifactWindow
from automation_harness.authoring.project import create_authoring_project
from automation_harness.authoring.step_registry import create_step_registry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_plan import save_plan
from automation_harness.formats import PLAN_SUFFIX, PROJECT_SUFFIX, REPOSITORY_SUFFIX, STEP_REGISTRY_SUFFIX
from automation_harness.models.plan import TestPlan


class StartWindow(ArtifactWindow):
    title_prefix = "Automation Harness"

    def open_artifact(self, path, *, project_context=None):
        opened = super().open_artifact(path, project_context=project_context)
        if opened is not None:
            self.window.iconify()
            opened.window.connect("destroy", lambda *_args: self._restore_after_artifact())
        return opened

    def _restore_after_artifact(self):
        self.window.deiconify()
        self.window.present()

    def __init__(self, *, opener=None):
        super().__init__(None, opener=opener)
        self.window.set_default_size(760, 520)
        self.toolbar.hide()

        heading = Gtk.Label(label="Automation Harness")
        heading.set_xalign(0)
        heading.set_markup("<span size='xx-large' weight='bold'>Automation Harness</span>")
        self.root.pack_start(heading, False, False, 8)

        subtitle = Gtk.Label(
            label="Open a Project to organize your automation work, or create a new one to get started."
        )
        subtitle.set_xalign(0)
        subtitle.set_line_wrap(True)
        self.root.pack_start(subtitle, False, False, 4)

        primary = Gtk.Box(spacing=12)
        self.root.pack_start(primary, False, False, 18)
        for label, callback in (
            ("Open Project", self.open_project),
            ("New Project", lambda: self.create_artifact("project")),
        ):
            button = Gtk.Button(label=label)
            button.set_size_request(300, 72)
            button.connect("clicked", lambda _button, fn=callback: fn())
            primary.pack_start(button, True, True, 0)

        secondary = Gtk.Box(spacing=12)
        self.root.pack_start(secondary, False, False, 0)

        open_artifact = Gtk.Button(label="Open Artifact…")
        open_artifact.set_size_request(210, 48)
        open_artifact.connect("clicked", lambda *_args: self.open_existing())
        secondary.pack_start(open_artifact, True, True, 0)

        new_menu = Gtk.Menu()
        for label, kind in (
            ("New Test Plan", "plan"),
            ("New Step Registry", "registry"),
            ("New Object Repository", "repository"),
        ):
            item = Gtk.MenuItem(label=label)
            item.connect("activate", lambda _item, selected_kind=kind: self.create_artifact(selected_kind))
            new_menu.append(item)
        new_menu.show_all()

        new_button = Gtk.MenuButton(label="New ▾")
        new_button.set_popup(new_menu)
        new_button.set_size_request(210, 48)
        secondary.pack_start(new_button, True, True, 0)

        preferences = Gtk.Button(label="Preferences")
        preferences.set_size_request(210, 48)
        preferences.connect("clicked", lambda *_args: self.preferences_dialog())
        self.root.pack_start(preferences, False, False, 0)

        note = Gtk.Label(
            label="Projects organize Test Plans, Step Registries, and Object Repositories so related artifacts can be managed together."
        )
        note.set_line_wrap(True)
        note.set_xalign(0)
        self.root.pack_start(note, False, False, 18)

    def open_project(self):
        path = self.choose_file(title="Open Automation Harness Project", suffix=PROJECT_SUFFIX)
        if path is None:
            return
        try:
            self.open_artifact(path)
        except Exception as exc:
            self.error("Open Project", "%s: %s" % (type(exc).__name__, exc))

    def open_existing(self):
        path = self.choose_file(title="Open Automation Harness artifact")
        if path is None:
            return
        try:
            self.open_artifact(path)
        except Exception as exc:
            self.error("Open Artifact", "%s: %s" % (type(exc).__name__, exc))

    def create_artifact(self, kind):
        if kind == "project":
            suffix, title = PROJECT_SUFFIX, "Create Project"
        elif kind == "plan":
            suffix, title = PLAN_SUFFIX, "Create Test Plan"
        elif kind == "registry":
            suffix, title = STEP_REGISTRY_SUFFIX, "Create Step Registry"
        else:
            suffix, title = REPOSITORY_SUFFIX, "Create Object Repository"

        path = self.choose_file(title=title, save=True, suffix=suffix)
        if path is None:
            return
        name = self.ask_text(title, "Name:", path.stem)
        if not name:
            return
        try:
            if kind == "project":
                create_authoring_project(path, name)
            elif kind == "plan":
                save_plan(TestPlan(name=name), path)
            elif kind == "registry":
                create_step_registry(path, name)
            else:
                ComponentRepository({}).save(path)
            self.open_artifact(path)
        except Exception as exc:
            self.error(title, "%s: %s" % (type(exc).__name__, exc))

    def save(self):
        return None
