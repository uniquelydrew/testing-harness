from __future__ import annotations

import json
import threading
from pathlib import Path

from automation_harness.authoring import capture_runtime
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.test_plan import validate_plan, validate_plan_components
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.runner.plan_execution import execute_plan


def _install_live_authoring(app_module) -> None:
    """Make the authoring console operate on the already-running environment."""
    Gtk = app_module.Gtk
    GLib = app_module.GLib

    original_build = app_module.AuthoringApp._build
    original_build_objects = app_module.AuthoringApp._build_objects

    def build(self):
        original_build(self)
        if self.mode != "author":
            return

        outer = self.window.get_child()
        children = outer.get_children() if outer is not None else []
        if len(children) >= 2:
            toolbar = children[0]
            target_toolbar = children[1]
            # Project/repository navigation remains useful. Target lifecycle
            # controls do not: the authoring console attaches to an environment
            # that is already up.
            self._button(toolbar, "New Project", self.new_project_dialog)
            self._button(toolbar, "Open Project", self.open_project_dialog)
            outer.remove(target_toolbar)
            target_toolbar.destroy()

        # Legacy project methods still write status text to this label. Keep a
        # harmless unparented instance for compatibility without exposing the
        # target concept in the authoring surface.
        self.target_label = Gtk.Label(label="")

    def build_objects(self):
        original_build_objects(self)
        if self.mode != "author":
            return
        left = self.objects_tab.get_child1()
        if left is None:
            return
        row = Gtk.Box(spacing=5)
        left.pack_start(row, False, False, 0)
        self._button(row, "Click Selected", self.click_selected_object)

    def bind_captured_application(self, captured):
        # Application identity remains part of the captured object's locator.
        # It is never promoted into a global test target.
        self._attached_application = None
        return True

    def click_selected_object(self):
        component_id = self._selected(self.object_tree)
        if not component_id:
            return self._info("Click", "Select a captured object first.")
        definition = self.repository.get(component_id)
        if not any(
            strategy.type in {"atspi", "java_accessibility", "javafx"}
            for strategy in definition.strategies
        ):
            return self._error(
                "Click",
                "The selected object has no interactive accessibility strategy.",
            )
        self._set_status("Clicking %s…" % component_id)
        threading.Thread(
            target=self._click_selected_worker,
            args=(definition,),
            name="automation-author-click",
            daemon=True,
        ).start()

    def click_selected_worker(self, definition):
        errors = []
        for strategy in definition.strategies:
            identification = strategy.options.get("identification")
            try:
                if strategy.type == "atspi":
                    result = AtspiDriver().activate(identification=identification)
                elif strategy.type == "java_accessibility":
                    result = JavaAccessibilityDriver().activate(identification=identification)
                elif strategy.type == "javafx":
                    result = JavaFxBridgeDriver().activate(identification=identification)
                else:
                    continue
            except Exception as exc:
                errors.append(
                    "%s: %s: %s" % (strategy.type, type(exc).__name__, exc)
                )
                continue
            GLib.idle_add(
                self._finish_selected_click,
                definition.component_id,
                result,
                None,
            )
            return
        GLib.idle_add(
            self._finish_selected_click,
            definition.component_id,
            None,
            RuntimeError("; ".join(errors) or "no compatible accessibility strategy"),
        )

    def finish_selected_click(self, component_id, result=None, error=None):
        if error is not None:
            self._set_status("Click failed")
            self._error("Click failed", "%s: %s" % (type(error).__name__, error))
            return False
        self._set_status("Clicked %s" % component_id)
        self._set_text(
            self.object_detail,
            json.dumps(
                {"component_id": component_id, "click": result},
                indent=2,
                default=str,
            ),
        )
        return False

    def run_reference_plan(self):
        if self._run_active:
            return
        # In authoring mode the environment is explicitly assumed to exist.
        # The startup/environment script belongs to external setup or CLI
        # execution, not to this console invocation.
        self.refresh_plan()
        issues = validate_plan(self.plan, self.registry)
        issues.extend(validate_plan_components(self.plan, self.repository))
        if issues:
            return self._error("Plan validation", "\n".join(issues))

        self._run_active = True
        self.run_reference_button.set_sensitive(False)
        self._set_status("Running test against the current desktop environment…")
        plan = self.plan
        runs_dir = (
            self.project.runs_dir
            if self.project is not None
            else (Path.cwd() / "runs").resolve()
        )

        def worker():
            try:
                result = execute_plan(
                    plan,
                    LiveDesktopBackend(),
                    runs_dir=runs_dir,
                    component_repository=self.repository,
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

    app_module.AuthoringApp._build = build
    app_module.AuthoringApp._build_objects = build_objects
    app_module.AuthoringApp.bind_captured_application = bind_captured_application
    app_module.AuthoringApp.click_selected_object = click_selected_object
    app_module.AuthoringApp._click_selected_worker = click_selected_worker
    app_module.AuthoringApp._finish_selected_click = finish_selected_click
    app_module.AuthoringApp.run_reference_plan = run_reference_plan


def run_author(argv=None):
    from automation_harness.authoring import app

    # Preserve all current capture/workbench behavior, then layer the live
    # environment semantics over the authoring-only surface.
    capture_runtime._install(app)
    _install_live_authoring(app)
    return app.main(argv)
