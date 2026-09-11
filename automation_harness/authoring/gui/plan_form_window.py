"""Field-oriented Test Plan call editing and concise flow summaries."""
from __future__ import annotations

import json
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.gui.plan_launch_window import LaunchRestoringTestPlanWindow
from automation_harness.models.plan import PlanVariableRef


class FormEditingTestPlanWindow(LaunchRestoringTestPlanWindow):
    """Test Plan editor that treats StepCall serialization as persistence, not UI."""

    def _build_flow(self, parent):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        parent.pack1(box, resize=True, shrink=False)
        row = Gtk.Box(spacing=6)
        box.pack_start(row, False, False, 0)
        row.pack_start(Gtk.Label(label="TEST FLOW"), False, False, 0)
        self.button("Move Up", lambda: self.move_selected(-1), parent=row)
        self.button("Move Down", lambda: self.move_selected(1), parent=row)
        self.button("Remove", self.remove_selected, parent=row)
        self.flow_tree, self.flow_store = self.list_tree(
            (("Group", 150), ("Node", 110), ("Step", 230), ("Parameters / Outputs", 420))
        )
        selection = self.flow_tree.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        selection.connect("changed", lambda *_args: self.show_flow_detail())
        box.pack_start(self.scrolled(self.flow_tree), True, True, 0)

    def refresh_flow(self):
        selected = self._selected_flow_node_ids() if hasattr(self, "flow_tree") else ()
        self.flow_store.clear()
        for call in self.plan.steps:
            self.flow_store.append((
                call.group or "Ungrouped",
                call.node_id,
                call.step_id,
                _call_summary(call),
            ))
        self._restore_flow_selection(selected)
        self.set_status(
            "%d authored calls • %d reusable definitions" %
            (len(self.plan.steps), len(self.reusable))
        )
        self.show_flow_detail()

    def show_flow_detail(self):
        node_ids = self._selected_flow_node_ids()
        if not node_ids:
            self.detail.get_buffer().set_text("")
            return
        selected = set(node_ids)
        calls = [item for item in self.plan.steps if item.node_id in selected]
        if len(calls) != 1:
            self.detail.get_buffer().set_text(
                "%d calls selected\n\n%s" % (
                    len(calls),
                    "\n".join("• %s — %s" % (item.node_id, _call_summary(item)) for item in calls),
                )
            )
            return
        call = calls[0]
        lines = [
            "Node: %s" % call.node_id,
            "Step: %s" % call.step_id,
            "Group: %s" % (call.group or "Ungrouped"),
            "",
            "Parameters:",
        ]
        if call.step_id == "gui.object.action":
            action = call.inputs.get("action", {})
            lines.append("  Target: %s" % call.inputs.get("component_id", ""))
            lines.append("  Action: %s" % (action.get("type", "") if isinstance(action, dict) else action))
            if isinstance(action, dict):
                for key, value in action.items():
                    if key != "type":
                        lines.append("  %s: %s" % (key, _display(value)))
        else:
            if call.inputs:
                for key, value in call.inputs.items():
                    lines.append("  %s: %s" % (key, _display(value)))
            else:
                lines.append("  none")
        lines.append("")
        lines.append("Outputs:")
        if call.outputs:
            for key, value in call.outputs.items():
                lines.append("  %s → %s" % (key, value))
        else:
            lines.append("  none")
        if call.depends_on:
            lines.extend(("", "Depends on: %s" % ", ".join(call.depends_on)))
        self.detail.get_buffer().set_text("\n".join(lines))

    def edit_selected_call(self):
        node_ids = self._selected_flow_node_ids()
        if not node_ids:
            return
        if len(node_ids) != 1:
            return self.info("Edit Call", "Select exactly one Test Flow call to edit.")
        node_id = node_ids[0]
        call = next(item for item in self.plan.steps if item.node_id == node_id)

        dialog = Gtk.Dialog(title="Edit Step", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save Changes", Gtk.ResponseType.OK)
        content = dialog.get_content_area()
        content.set_spacing(8)
        content.set_border_width(10)
        grid = Gtk.Grid()
        grid.set_row_spacing(7)
        grid.set_column_spacing(10)
        content.pack_start(grid, True, True, 0)
        row = 0

        row = _readonly_row(grid, row, "Node", call.node_id)
        row = _readonly_row(grid, row, "Step", call.step_id)
        group_entry = Gtk.Entry()
        group_entry.set_text(call.group or "")
        row = _entry_row(grid, row, "Group", group_entry)

        input_fields = {}
        action_type_entry = None
        action_parameter_fields = {}
        component_entry = None

        if call.step_id == "gui.object.action":
            component_entry = Gtk.ComboBoxText.new_with_entry()
            known = sorted(self.repository.components)
            current_component = str(call.inputs.get("component_id", ""))
            for component_id in known:
                component_entry.append_text(component_id)
            component_entry.get_child().set_text(current_component)
            row = _entry_row(grid, row, "Object", component_entry)

            action = call.inputs.get("action", {})
            if not isinstance(action, dict):
                action = {"type": str(action)}
            action_type_entry = Gtk.Entry()
            action_type_entry.set_text(str(action.get("type", "")))
            row = _entry_row(grid, row, "Action", action_type_entry)
            for name, value in action.items():
                if name == "type":
                    continue
                entry = Gtk.Entry()
                entry.set_text(_editable(value))
                row = _entry_row(grid, row, _humanize(name), entry)
                action_parameter_fields[name] = (entry, value)
        else:
            for name, value in call.inputs.items():
                entry = Gtk.Entry()
                entry.set_text(_editable(value))
                entry.set_placeholder_text("value, JSON, or ${plan.variable}")
                row = _entry_row(grid, row, _humanize(name), entry)
                input_fields[name] = (entry, value)

        output_fields = {}
        if call.outputs:
            separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            grid.attach(separator, 0, row, 2, 1)
            row += 1
            label = Gtk.Label(label="Output bindings")
            label.set_xalign(0)
            grid.attach(label, 0, row, 2, 1)
            row += 1
            for name, value in call.outputs.items():
                entry = Gtk.Entry()
                entry.set_text(str(value))
                row = _entry_row(grid, row, _humanize(name), entry)
                output_fields[name] = entry

        dependencies_entry = Gtk.Entry()
        dependencies_entry.set_text(", ".join(call.depends_on))
        dependencies_entry.set_placeholder_text("node-id, node-id")
        row = _entry_row(grid, row, "Depends on", dependencies_entry)

        dialog.set_default_size(620, -1)
        dialog.show_all()
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return

        try:
            if call.step_id == "gui.object.action":
                component_id = component_entry.get_child().get_text().strip()
                action_type = action_type_entry.get_text().strip()
                if not component_id:
                    raise ValueError("Object is required.")
                if not action_type:
                    raise ValueError("Action is required.")
                action = {"type": action_type}
                for name, (entry, original) in action_parameter_fields.items():
                    action[name] = _parse_editor_value(entry.get_text(), original)
                inputs = {"component_id": component_id, "action": action}
            else:
                inputs = {
                    name: _parse_editor_value(entry.get_text(), original)
                    for name, (entry, original) in input_fields.items()
                }
            outputs = {
                name: entry.get_text().strip()
                for name, entry in output_fields.items()
                if entry.get_text().strip()
            }
            depends_on = tuple(
                value.strip() for value in dependencies_entry.get_text().split(",") if value.strip()
            )
            updated = replace(
                call,
                group=group_entry.get_text().strip(),
                inputs=inputs,
                outputs=outputs,
                depends_on=depends_on,
            )
            self.plan = replace(
                self.plan,
                steps=tuple(updated if item.node_id == node_id else item for item in self.plan.steps),
            )
            self.mark_dirty()
            self.refresh_all()
            self._restore_flow_selection((node_id,))
            # "Save Changes" is intentionally a persistence boundary, not just
            # a transient dialog apply.  This prevents edited calls from being
            # lost if the author closes the Plan immediately afterward.
            self.save()
            self._restore_flow_selection((node_id,))
            self.show_flow_detail()
        except Exception as exc:
            dialog.destroy()
            return self.error("Edit Step", "%s: %s" % (type(exc).__name__, exc))
        dialog.destroy()


def _readonly_row(grid, row, label_text, value):
    label = Gtk.Label(label=label_text)
    label.set_xalign(0)
    grid.attach(label, 0, row, 1, 1)
    value_label = Gtk.Label(label=str(value))
    value_label.set_xalign(0)
    value_label.set_selectable(True)
    grid.attach(value_label, 1, row, 1, 1)
    return row + 1


def _entry_row(grid, row, label_text, widget):
    label = Gtk.Label(label=label_text)
    label.set_xalign(0)
    label.set_size_request(150, -1)
    grid.attach(label, 0, row, 1, 1)
    widget.set_hexpand(True)
    grid.attach(widget, 1, row, 1, 1)
    return row + 1


def _humanize(value):
    return str(value).replace("_", " ").strip().title()


def _display(value):
    if isinstance(value, PlanVariableRef):
        return "${%s}" % value.name
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, separators=(",", ":"))


def _editable(value):
    if isinstance(value, PlanVariableRef):
        return "${%s}" % value.name
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _parse_editor_value(raw, original):
    raw = raw.strip()
    if raw.startswith("${") and raw.endswith("}"):
        return PlanVariableRef(raw[2:-1].strip())
    if isinstance(original, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _call_summary(call):
    pieces = []
    if call.step_id == "gui.object.action":
        target = call.inputs.get("component_id")
        action = call.inputs.get("action", {})
        if target:
            pieces.append("object=%s" % target)
        if isinstance(action, dict):
            action_type = action.get("type")
            if action_type:
                pieces.append("action=%s" % action_type)
            for key, value in action.items():
                if key != "type":
                    pieces.append("%s=%s" % (key, _display(value)))
        elif action:
            pieces.append("action=%s" % _display(action))
    else:
        for key, value in call.inputs.items():
            pieces.append("%s=%s" % (key, _display(value)))
    for key, value in call.outputs.items():
        pieces.append("%s→%s" % (key, value))
    return "; ".join(pieces) if pieces else "—"
