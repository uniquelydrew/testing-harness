"""Test Plan interaction polish and run lifecycle behavior."""
from __future__ import annotations

import json
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.gui.plan_visual_window import VisualTestPlanWindow
from automation_harness.authoring.gui.plan_window import _decode, _encode
from automation_harness.authoring.project import AuthoringProject
from automation_harness.authoring.project_registry_service import save_plan_selection_to_project_registry
from automation_harness.authoring.step_registry import load_step_registry_resources
from automation_harness.core.test_plan import repository_from_plan


class LaunchRestoringTestPlanWindow(VisualTestPlanWindow):
    """Test Plan window with stable multi-selection and launcher restoration."""

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
            (("Group", 150), ("Node", 110), ("Step", 250), ("Inputs", 250))
        )
        selection = self.flow_tree.get_selection()
        selection.set_mode(Gtk.SelectionMode.MULTIPLE)
        selection.connect("changed", lambda *_args: self.show_flow_detail())
        box.pack_start(self.scrolled(self.flow_tree), True, True, 0)

    def _selected_flow_node_ids(self):
        selection = self.flow_tree.get_selection()
        model, paths = selection.get_selected_rows()
        return tuple(model[path][1] for path in paths)

    def _restore_flow_selection(self, node_ids):
        wanted = set(node_ids)
        selection = self.flow_tree.get_selection()
        selection.unselect_all()
        if not wanted:
            return
        model = self.flow_tree.get_model()
        iterator = model.get_iter_first()
        first_path = None
        while iterator is not None:
            if model.get_value(iterator, 1) in wanted:
                selection.select_iter(iterator)
                if first_path is None:
                    first_path = model.get_path(iterator)
            iterator = model.iter_next(iterator)
        if first_path is not None:
            self.flow_tree.scroll_to_cell(first_path, None, False, 0.0, 0.0)

    def refresh_flow(self):
        selected = self._selected_flow_node_ids() if hasattr(self, "flow_tree") else ()
        self.flow_store.clear()
        for call in self.plan.steps:
            self.flow_store.append((
                call.group or "Ungrouped",
                call.node_id,
                call.step_id,
                json.dumps(_encode(call.inputs), separators=(",", ":")),
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
        if len(calls) == 1:
            self.detail.get_buffer().set_text(
                json.dumps(calls[0].to_dict(), indent=2, default=str)
            )
            return
        self.detail.get_buffer().set_text(json.dumps({
            "selected_calls": len(calls),
            "nodes": [item.node_id for item in calls],
            "steps": [item.step_id for item in calls],
            "groups": list(dict.fromkeys(item.group or "Ungrouped" for item in calls)),
        }, indent=2))

    def move_selected(self, offset):
        if offset not in (-1, 1):
            raise ValueError("step movement offset must be -1 or 1")
        node_ids = self._selected_flow_node_ids()
        if not node_ids:
            return
        selected = set(node_ids)
        steps = list(self.plan.steps)
        changed = False
        if offset < 0:
            for index in range(1, len(steps)):
                if steps[index].node_id in selected and steps[index - 1].node_id not in selected:
                    steps[index - 1], steps[index] = steps[index], steps[index - 1]
                    changed = True
        else:
            for index in range(len(steps) - 2, -1, -1):
                if steps[index].node_id in selected and steps[index + 1].node_id not in selected:
                    steps[index], steps[index + 1] = steps[index + 1], steps[index]
                    changed = True
        if not changed:
            return
        self.plan = replace(self.plan, steps=tuple(steps))
        self.mark_dirty()
        self.refresh_all()
        self._restore_flow_selection(node_ids)
        self.show_flow_detail()

    def remove_selected(self):
        node_ids = self._selected_flow_node_ids()
        if not node_ids:
            return
        selected = set(node_ids)
        self.plan = replace(
            self.plan,
            steps=tuple(item for item in self.plan.steps if item.node_id not in selected),
        )
        self.mark_dirty()
        self.refresh_all()

    def edit_selected_call(self):
        node_ids = self._selected_flow_node_ids()
        if not node_ids:
            return
        if len(node_ids) != 1:
            return self.info("Edit Call", "Select exactly one Test Flow call to edit.")
        node_id = node_ids[0]
        call = next(item for item in self.plan.steps if item.node_id == node_id)
        dialog = Gtk.Dialog(title="Edit Call", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Apply", Gtk.ResponseType.OK)
        text = Gtk.TextView()
        text.set_monospace(True)
        text.get_buffer().set_text(json.dumps(call.to_dict(), indent=2, default=str))
        scroll = self.scrolled(text)
        scroll.set_size_request(700, 520)
        dialog.get_content_area().pack_start(scroll, True, True, 0)
        dialog.show_all()
        response = dialog.run()
        buffer = text.get_buffer()
        raw = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        try:
            payload = json.loads(raw)
            updated = replace(
                call,
                group=str(payload.get("group", call.group)),
                inputs=_decode(payload.get("inputs", {})),
                outputs={str(k): str(v) for k, v in payload.get("outputs", {}).items()},
                depends_on=tuple(payload.get("depends_on", call.depends_on)),
            )
            self.plan = replace(
                self.plan,
                steps=tuple(updated if item.node_id == node_id else item for item in self.plan.steps),
            )
        except Exception as exc:
            return self.error("Edit Call", str(exc))
        self.mark_dirty()
        self.refresh_all()
        self._restore_flow_selection((node_id,))

    def save_group_to_registry(self):
        if not self.project_context:
            return self.info(
                "Step Registry",
                "Open this Test Plan through a Project to save reusable compositions into Project Registries.",
            )
        node_ids = self._selected_flow_node_ids()
        if not node_ids:
            return self.info("Step Registry", "Select a Test Flow call first.")
        selected_ids = set(node_ids)
        selected_calls = [item for item in self.plan.steps if item.node_id in selected_ids]
        groups = {item.group for item in selected_calls if item.group}
        if not groups:
            return self.info("Step Registry", "The selected call(s) are not part of a composed group.")
        if len(groups) != 1 or any(not item.group for item in selected_calls):
            return self.info(
                "Step Registry",
                "Selected calls must all belong to the same composed group.",
            )
        group = next(iter(groups))
        project = AuthoringProject.load(self.project_context)
        if not project.step_registries:
            return self.info(
                "Step Registry",
                "Create or add a Step Registry from the Project window first.",
            )
        registry_path = project.step_registries[0]
        step_id = self.ask_text("Save Group to Registry", "Reusable step ID:")
        if not step_id:
            return
        name = self.ask_text("Save Group to Registry", "Display name:", group)
        if not name:
            return
        try:
            project, _registry = save_plan_selection_to_project_registry(
                self.project_context,
                self.plan,
                source_repository=self.repository,
                registry_path=registry_path,
                step_id=step_id,
                name=name,
                group=group,
            )
            self.project = project
            self.registry_resources = load_step_registry_resources(project.step_registries)
            self.reusable = dict(self.registry_resources.steps)
            self.repository = repository_from_plan(self.plan).overlay(self.registry_resources.repository)
        except Exception as exc:
            return self.error("Save Group to Registry", "%s: %s" % (type(exc).__name__, exc))
        self.refresh_all()
        self._restore_flow_selection(node_ids)
        self.set_status("Saved %s to %s" % (name, registry_path.name))

    def _run_finished(self, result, error):
        launching_window = getattr(self, "launching_window", None)
        if launching_window is not None:
            try:
                launching_window.deiconify()
                launching_window.present()
            except Exception:
                # The launcher may have been closed while the test was running.
                # That must not interfere with completion handling for the Plan.
                pass
        return super()._run_finished(result, error)
