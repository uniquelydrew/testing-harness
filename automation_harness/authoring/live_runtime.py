from __future__ import annotations

import json
import re
import threading
from collections.abc import Mapping

from automation_harness.authoring import capture_runtime
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver


def _install_workbench_controls() -> None:
    """Add first-class regex mode and direct clicking to object authoring."""
    from automation_harness.authoring import object_identity_workbench as workbench

    if getattr(workbench, "_live_workbench_controls_installed", False):
        return

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
    workbench._live_workbench_controls_installed = True


def run_author(argv=None):
    from automation_harness.authoring import app

    capture_runtime._install(app)
    _install_workbench_controls()
    return app.main(argv)
