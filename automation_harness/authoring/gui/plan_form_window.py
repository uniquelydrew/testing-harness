"""Field-oriented Test Plan call editing and concise flow summaries."""
from __future__ import annotations

import json
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.gui.action_widgets import (
    create_action_input_widget,
    interaction_actions,
    read_action_input,
)
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
        self.flow_tree, self.flow_store = self.list_tree(
            (("Group", 150), ("Node", 110), ("Step", 230), ("Parameters / Outputs", 420))
        )
        selection = self.flow_tree.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        selection.connect("changed", lambda *_args: self.show_flow_detail())
        self.flow_tree.connect("row-activated", lambda *_args: self.edit_selected_call())
        self.flow_tree.connect("button-press-event", self._flow_button_press)
        self.flow_tree.connect("key-press-event", self._flow_key_press)
        self.flow_tree.set_reorderable(True)
        self.flow_store.connect("rows-reordered", lambda *_args: self._flow_reordered())
        box.pack_start(self.scrolled(self.flow_tree), True, True, 0)

    def refresh_flow(self):
        selected = self._selected_flow_node_ids() if hasattr(self, "flow_tree") else ()
        self.flow_store.clear()
        for call in self.plan.steps:
            self.flow_store.append((
                call.group or "Ungrouped",
                call.node_id,
                call.name or call.step_id,
                _call_summary(call, self.repository),
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
                    "\n".join(
                        "• %s — %s" % (item.node_id, _call_summary(item, self.repository))
                        for item in calls
                    ),
                )
            )
            return
        call = calls[0]
        lines = [
            "Node: %s" % call.node_id,
            "Step: %s" % call.step_id,
            "Name: %s" % (call.name or "—"),
            "Description: %s" % (call.description or "—"),
            "Group: %s" % (call.group or "Ungrouped"),
            "",
            "Parameters:",
        ]
        if call.step_id == "gui.object.action":
            action = call.inputs.get("action", {})
            target = call.inputs.get("component_id", "")
            if target and self.repository.contains(target):
                target = self.repository.get(target).component_id
            lines.append("  Target: %s" % target)
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
        self.refresh_objects()
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
        name_entry = Gtk.Entry()
        name_entry.set_text(call.name or "")
        name_entry.set_placeholder_text("Readable step name")
        row = _entry_row(grid, row, "Name", name_entry)
        description_view = Gtk.TextView()
        description_view.set_wrap_mode(Gtk.WrapMode.WORD)
        description_view.get_buffer().set_text(call.description or "")
        description_scroll = Gtk.ScrolledWindow()
        description_scroll.set_size_request(-1, 90)
        description_scroll.add(description_view)
        row = _entry_row(grid, row, "Description", description_scroll)
        group_entry = Gtk.Entry()
        group_entry.set_text(call.group or "")
        row = _entry_row(grid, row, "Group", group_entry)

        input_fields = {}
        action_combo = None
        action_input_fields = {}
        component_entry = None

        if call.step_id == "gui.object.action":
            component_entry = Gtk.ComboBoxText()
            known = sorted(self.repository.components)
            current_component = str(call.inputs.get("component_id", ""))
            if current_component and self.repository.contains(current_component):
                current_component = self.repository.get(current_component).component_id
            for component_id in known:
                component_entry.append(component_id, component_id)
            if current_component and current_component not in known:
                component_entry.append(current_component, current_component)
            component_entry.set_active_id(current_component if current_component else (known[0] if known else None))
            row = _entry_row(grid, row, "Object", component_entry)

            action = call.inputs.get("action", {})
            if not isinstance(action, dict):
                action = {"type": str(action)}
            current_action_type = str(action.get("type", ""))
            action_combo = Gtk.ComboBoxText()
            row = _entry_row(grid, row, "Action", action_combo)
            action_inputs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            row = _entry_row(grid, row, "Action Inputs", action_inputs_box)

            def rebuild_action_inputs(*_args):
                for child in action_inputs_box.get_children():
                    child.destroy()
                action_input_fields.clear()
                component_id = component_entry.get_active_id()
                action_id = action_combo.get_active_id()
                if not component_id or not action_id or not self.repository.contains(component_id):
                    action_inputs_box.show_all()
                    return
                component = self.repository.get(component_id)
                definition = next(
                    (
                        item for item in interaction_actions(component)
                        if item.action_id == action_id
                    ),
                    None,
                )
                if definition is None:
                    action_inputs_box.show_all()
                    return
                preserve = action if action_id == current_action_type else {}
                for item in definition.inputs:
                    current = preserve.get(item.name) if isinstance(preserve, dict) else None
                    widget = create_action_input_widget(item, component, current=current)
                    line = Gtk.Box(spacing=6)
                    label = Gtk.Label(label=item.name + (" *" if item.required else ""))
                    label.set_size_request(160, -1); label.set_xalign(0)
                    line.pack_start(label, False, False, 0)
                    line.pack_start(widget, True, True, 0)
                    action_inputs_box.pack_start(line, False, False, 0)
                    action_input_fields[item.name] = (item, widget)
                action_inputs_box.show_all()

            def rebuild_action_choices(*_args):
                action_combo.remove_all()
                component_id = component_entry.get_active_id()
                if not component_id or not self.repository.contains(component_id):
                    rebuild_action_inputs()
                    return
                definitions = interaction_actions(self.repository.get(component_id))
                for definition in definitions:
                    action_combo.append(
                        definition.action_id,
                        "%s — %s" % (definition.name, definition.category),
                    )
                if current_action_type and any(
                    item.action_id == current_action_type for item in definitions
                ):
                    action_combo.set_active_id(current_action_type)
                elif definitions:
                    action_combo.set_active(0)
                else:
                    rebuild_action_inputs()

            component_entry.connect("changed", rebuild_action_choices)
            action_combo.connect("changed", rebuild_action_inputs)
            rebuild_action_choices()
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
                component_id = component_entry.get_active_id()
                action_type = action_combo.get_active_id()
                if not component_id or not self.repository.contains(component_id):
                    raise ValueError("A repository Object is required.")
                if not action_type:
                    raise ValueError("A supported Action is required.")
                action = {"type": action_type}
                for name, (item, widget) in action_input_fields.items():
                    present, value = read_action_input(item, widget)
                    if not present:
                        if item.required:
                            raise ValueError("missing required input %s" % name)
                        continue
                    action[name] = value
                component_reference = self.repository.get(component_id).component_id
                inputs = {"component_id": component_reference, "action": action}
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
                name=name_entry.get_text().strip(),
                description=description_view.get_buffer().get_text(
                    description_view.get_buffer().get_start_iter(),
                    description_view.get_buffer().get_end_iter(), True,
                ).strip(),
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


def _call_summary(call, repository=None):
    pieces = []
    if call.step_id == "gui.object.action":
        target = call.inputs.get("component_id")
        if target and repository is not None and repository.contains(target):
            target = repository.get(target).component_id
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
