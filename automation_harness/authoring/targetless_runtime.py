"""Final targetless overrides for the legacy GTK authoring shell.

The base authoring module predates live-desktop execution and still contains
unreachable target lifecycle controls. This adapter prevents those legacy
implementation details from imposing fields or behavior on the current project
model while the base UI is incrementally decomposed.
"""
from __future__ import annotations

from pathlib import Path

from automation_harness.authoring.project import AuthoringProject, create_authoring_project


def install(app_module) -> None:
    if getattr(app_module, "_targetless_runtime_installed", False):
        return

    live_build = app_module.AuthoringApp._build

    def build(self):
        # The legacy base builder conditionally reads project.target only to
        # construct a toolbar that the live-runtime patch immediately removes.
        # Hide the project for that construction pass, then restore it before
        # any real authoring behavior is available.
        project = self.project
        self.project = None
        try:
            live_build(self)
        finally:
            self.project = project

        # These were lifecycle caches for an application-level target. They are
        # no longer part of live authoring state.
        for name in ("_target_backend", "_target_environment", "_attached_application", "target_label"):
            if hasattr(self, name):
                try:
                    delattr(self, name)
                except AttributeError:
                    pass

    def new_project_dialog(self):
        path = self._choose_file(save=True, yaml=True)
        if not path:
            return
        name = self._ask_text("New Test Project", "Project name:", Path(path).stem)
        if not name:
            return
        try:
            self.project_path = Path(path)
            self.project = create_authoring_project(self.project_path, name)
            self.repository_path = self.project.repository
            self.repository = self._load_repository()
        except Exception as exc:
            return self._error("New project", "%s: %s" % (type(exc).__name__, exc))
        self.refresh_all()
        self._set_status("Created project — capture an object or add a registered script step")

    def open_project_dialog(self):
        path = self._choose_file(yaml=True)
        if not path:
            return
        try:
            project = AuthoringProject.load(Path(path))
            self.project_path = Path(path)
            self.project = project
            self.repository_path = project.repository
            self.repository = self._load_repository()
        except Exception as exc:
            return self._error("Open project", "%s: %s" % (type(exc).__name__, exc))
        self.refresh_all()
        self._set_status("Opened project: %s" % project.name)

    def bind_captured_application(self, _captured):
        # Capture keeps application/window ownership inside the object's own
        # identity. There is no project-level binding operation.
        return True

    app_module.AuthoringApp._build = build
    app_module.AuthoringApp.new_project_dialog = new_project_dialog
    app_module.AuthoringApp.open_project_dialog = open_project_dialog
    app_module.AuthoringApp.bind_captured_application = bind_captured_application
    app_module._targetless_runtime_installed = True
