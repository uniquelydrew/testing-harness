from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping
from pathlib import Path

from automation_harness.authoring import capture_runtime
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.test_plan import validate_plan, validate_plan_components
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
from automation_harness.runner.plan_execution import execute_plan


def _install_workbench_controls() -> None:
    """Add first-class regex mode and direct clicking to object authoring."""
    from automation_harness.authoring import object_identity_workbench as workbench

    Gtk = workbench.Gtk
    GLib = workbench.GLib
    original_build = workbench.ObjectIdentityWorkbench._build
    original_flatten = workbench._flatten

    def flatten(value, path=()):
        # Regex is a scalar matcher specification, not another level of object
        # identity structure.
        if isinstance(value, Mapping) and set(value) == {"regex"}:
            yield path, value
            return
        for item in original_flatten(value, path):
            yield item

    def build(self):
        original_build(self)
        outer = self.window.get_child()
        children = outer.get_children() if outer is not None else []
        if children:
            toolbar = children[0]
            self._button(toolbar, "Click", self.click_selected)

    def add_property_row(self, grid, row, section, path, logical_key, value, policy, known_classes):
        check = Gtk.CheckButton()
        check.set_active(bool(policy.selected))
        check.set_sensitive(bool(policy.selectable))
        grid.attach(check, 0, row, 1, 1)

        key_label = Gtk.Label(label=logical_key)
        key_label.set_halign(Gtk.Align.START)
        grid.attach(key_label, 1, row, 1, 1)
        grid.attach(Gtk.Label(label="="), 2, row, 1, 1)

        is_regex = isinstance(value, Mapping) and set(value) == {"regex"}
        display_value = value.get("regex") if is_regex else value
        field, entry = workbench._value_field(logical_key, display_value, known_classes)
        field.set_hexpand(True)
        field.set_sensitive(bool(policy.selectable))
        grid.attach(field, 3, row, 1, 1)

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        regex_capable = isinstance(display_value, str)
        match_mode = None
        if regex_capable and policy.selectable:
            match_mode = Gtk.ComboBoxText()
            match_mode.append("exact", "Exact")
            match_mode.append("regex", "Regex")
            match_mode.set_active_id("regex" if is_regex else "exact")
            detail.pack_start(match_mode, False, False, 0)
        status = Gtk.Label(label="%s · %s" % (policy.stability, policy.reason))
        status.set_halign(Gtk.Align.START)
        detail.pack_start(status, False, False, 0)
        grid.attach(detail, 4, row, 1, 1)

        if not policy.selectable:
            key_label.set_sensitive(False)
            status.set_sensitive(False)

        self.identity_fields.append(
            (section, path, value, check, entry, policy.selectable, match_mode)
        )
        return row + 1

    def current_identity(self):
        leaves = {"mandatory": {}, "assistive": {}}
        for section, path, original, check, entry, _selectable, match_mode in self.identity_fields:
            if not check.get_active():
                continue
            text = entry.get_text().strip()
            mode = match_mode.get_active_id() if match_mode is not None else "exact"
            if mode == "regex":
                if not text:
                    raise ValueError(
                        "%s.%s regex requires a non-empty pattern"
                        % (section, workbench._path_text(path))
                    )
                try:
                    re.compile(text)
                except re.error as exc:
                    raise ValueError(
                        "%s.%s has invalid regex: %s"
                        % (section, workbench._path_text(path), exc)
                    )
                parsed = {"regex": text}
            else:
                parse_original = original
                if isinstance(original, Mapping) and set(original) == {"regex"}:
                    parse_original = str(original.get("regex") or "")
                parsed = workbench._parse_value(
                    text,
                    parse_original,
                    "%s.%s" % (section, workbench._path_text(path)),
                )
            leaves[section][path] = parsed

        mandatory = workbench._rebuild_from_paths(leaves["mandatory"])
        assistive = workbench._rebuild_from_paths(leaves["assistive"])
        if not mandatory:
            raise ValueError("At least one mandatory identity condition is required.")
        result = {"mandatory": mandatory}
        if assistive:
            result["assistive"] = assistive
        if self.ordinal_field is not None:
            enabled, spin = self.ordinal_field
            if enabled.get_active():
                result["ordinal"] = int(spin.get_value_as_int())
        return result

    def finish_workbench_click(self, result=None, error=None):
        if error is not None:
            self._set_status("Click failed")
            self.app._error("Click failed", "%s: %s" % (type(error).__name__, error))
            return False
        self._set_status("Clicked selected object")
        self.app._set_text(
            self.app.object_detail,
            json.dumps({"click": result}, indent=2, default=str),
        )
        return False

    def click_selected(self):
        node = self._selected_node()
        if node is None:
            return
        try:
            captured = self._captured_for_node(node)
            strategy = captured.candidate_strategy()
            if strategy.type == "javafx":
                identification = strategy.options.get("identification")
            elif strategy.type in {"atspi", "java_accessibility"}:
                identification = captured.candidate_identification().to_dict()
            else:
                return self.app._error(
                    "Click",
                    "The selected object has no interactive accessibility strategy.",
                )
        except Exception as exc:
            return self.app._error("Click", "%s: %s" % (type(exc).__name__, exc))

        self._set_status("Clicking selected object…")

        def worker():
            try:
                if strategy.type == "atspi":
                    result = AtspiDriver().activate(identification=identification)
                elif strategy.type == "java_accessibility":
                    result = JavaAccessibilityDriver().activate(identification=identification)
                else:
                    result = JavaFxBridgeDriver().activate(identification=identification)
            except Exception as exc:
                GLib.idle_add(self._finish_workbench_click, None, exc)
                return
            GLib.idle_add(self._finish_workbench_click, result, None)

        threading.Thread(
            target=worker,
            name="automation-workbench-click",
            daemon=True,
        ).start()

    workbench._flatten = flatten
    workbench.ObjectIdentityWorkbench._build = build
    workbench.ObjectIdentityWorkbench._add_property_row = add_property_row
    workbench.ObjectIdentityWorkbench.current_identity = current_identity
    workbench.ObjectIdentityWorkbench._finish_workbench_click = finish_workbench_click
    workbench.ObjectIdentityWorkbench.click_selected = click_selected


def _install_live_authoring(app_module) -> None:
    """Make the authoring console operate on the already-running environment."""
    Gtk = app_module.Gtk
    GLib = app_module.GLib

    original_build = app_module.AuthoringApp._build
    original_build_objects = app_module.AuthoringApp._build_objects
    original_new_project_dialog = app_module.AuthoringApp.new_project_dialog
    original_open_project_dialog = app_module.AuthoringApp.open_project_dialog

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

    def new_project_dialog(self):
        previous = self.project
        original_new_project_dialog(self)
        if self.project is not None and self.project is not previous:
            self._attached_application = None
            self._set_status(
                "Created project — current environment is assumed running; capture an object to begin"
            )

    def open_project_dialog(self):
        previous_path = self.project_path
        original_open_project_dialog(self)
        if self.project is not None and self.project_path != previous_path:
            self._attached_application = None
            self._set_status(
                "Opened project: %s — current environment attached" % self.project.name
            )

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
    app_module.AuthoringApp.new_project_dialog = new_project_dialog
    app_module.AuthoringApp.open_project_dialog = open_project_dialog
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
    _install_workbench_controls()
    _install_live_authoring(app)
    return app.main(argv)
