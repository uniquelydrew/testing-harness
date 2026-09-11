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

    def __init__(self, *, opener=None):
        super().__init__(None, opener=opener)
        self.window.set_default_size(760, 520)
        self.toolbar.hide()
        heading = Gtk.Label(label="Automation Harness")
        heading.set_xalign(0)
        heading.set_markup("<span size='xx-large' weight='bold'>Automation Harness</span>")
        self.root.pack_start(heading, False, False, 8)
        subtitle = Gtk.Label(label="Open or create an authoring artifact. Each artifact opens in its dedicated workflow.")
        subtitle.set_xalign(0); self.root.pack_start(subtitle, False, False, 4)

        grid = Gtk.Grid(column_spacing=12, row_spacing=12); self.root.pack_start(grid, False, False, 18)
        actions = (
            ("Open Artifact…", self.open_existing),
            ("New Project", lambda: self.create_artifact("project")),
            ("New Test Plan", lambda: self.create_artifact("plan")),
            ("New Step Registry", lambda: self.create_artifact("registry")),
            ("New Object Repository", lambda: self.create_artifact("repository")),
            ("Preferences", self.preferences_dialog),
        )
        for index, (label, callback) in enumerate(actions):
            button = Gtk.Button(label=label); button.set_size_request(260, 54); button.connect("clicked", lambda _button, fn=callback: fn()); grid.attach(button, index % 2, index // 2, 1, 1)
        note = Gtk.Label(label="Projects organize artifacts; Test Plans, Step Registries, and Object Repositories remain independently openable.")
        note.set_line_wrap(True); note.set_xalign(0); self.root.pack_start(note, False, False, 12)

    def open_existing(self):
        path = self.choose_file(title="Open Automation Harness artifact")
        if path is None: return
        try: self.open_artifact(path)
        except Exception as exc: self.error("Open Artifact", "%s: %s" % (type(exc).__name__, exc))

    def create_artifact(self, kind):
        if kind == "project": suffix, title = PROJECT_SUFFIX, "Create Project"
        elif kind == "plan": suffix, title = PLAN_SUFFIX, "Create Test Plan"
        elif kind == "registry": suffix, title = STEP_REGISTRY_SUFFIX, "Create Step Registry"
        else: suffix, title = REPOSITORY_SUFFIX, "Create Object Repository"
        path = self.choose_file(title=title, save=True, suffix=suffix)
        if path is None: return
        name = self.ask_text(title, "Name:", path.stem)
        if not name: return
        try:
            if kind == "project": create_authoring_project(path, name)
            elif kind == "plan": save_plan(TestPlan(name=name), path)
            elif kind == "registry": create_step_registry(path, name)
            else: ComponentRepository({}).save(path)
            self.open_artifact(path)
        except Exception as exc: self.error(title, "%s: %s" % (type(exc).__name__, exc))

    def save(self):
        return None
