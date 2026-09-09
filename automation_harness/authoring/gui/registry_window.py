from __future__ import annotations

import json
from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.authoring.gui.common import ArtifactWindow
from automation_harness.authoring.project import AuthoringProject
from automation_harness.authoring.step_registry import AuthoringStepRegistry, load_step_registry_resources, save_step_registry
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.core.step_registry import default_step_registry
from automation_harness.models.plan import StepCall, TestPlan


class StepRegistryWindow(ArtifactWindow):
    title_prefix = "Automation Harness Step Registry"

    def __init__(self, path, *, project_context=None, opener=None):
        super().__init__(path, project_context=project_context, opener=opener)
        self.registry = AuthoringStepRegistry.load(self.path)
        self.runtime_registry = default_step_registry()
        self.project = AuthoringProject.load(self.project_context) if self.project_context else None
        self.project_resources = load_step_registry_resources(self.project.step_registries) if self.project and self.project.step_registries else None
        self.button("Save", self.save)
        self.button("New Step", self.new_step)
        self.button("Duplicate", self.duplicate_step)
        self.button("Remove", self.remove_step)
        self.button("Open Object Repository", lambda: self.open_artifact(self.registry.repository, project_context=self.project_context))
        if self.project_context:
            self.button("Open Project", lambda: self.open_artifact(self.project_context, project_context=self.project_context))

        outer = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL); self.root.pack_start(outer, True, True, 0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); outer.pack1(left, resize=False, shrink=False)
        left.pack_start(Gtk.Label(label="REGISTRY STEPS"), False, False, 0)
        self.search = Gtk.SearchEntry(); self.search.set_placeholder_text("Step name, ID or description"); self.search.connect("search-changed", lambda *_args: self.refresh_steps()); left.pack_start(self.search, False, False, 0)
        self.step_tree, self.step_store = self.list_tree((("Step", 190), ("ID", 230)))
        self.step_tree.get_selection().connect("changed", lambda *_args: self.refresh_selected())
        left.pack_start(self.scrolled(self.step_tree), True, True, 0)

        middle_right = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL); outer.pack2(middle_right, resize=True, shrink=False)
        middle = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); middle_right.pack1(middle, resize=True, shrink=False)
        row = Gtk.Box(spacing=6); row.pack_start(Gtk.Label(label="COMPOSITION"), False, False, 0); middle.pack_start(row, False, False, 0)
        self.button("Add Call", self.add_call, parent=row)
        self.flow_tree, self.flow_store = self.list_tree((("Node", 120), ("Step", 260), ("Inputs", 260)))
        self.flow_tree.get_selection().connect("changed", lambda *_args: self.show_call())
        middle.pack_start(self.scrolled(self.flow_tree), True, True, 0)
        buttons = Gtk.Box(spacing=6); middle.pack_start(buttons, False, False, 0)
        self.button("Edit Call", self.edit_call, parent=buttons)
        self.button("Remove Call", self.remove_call, parent=buttons)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6); middle_right.pack2(right, resize=True, shrink=False)
        right.pack_start(Gtk.Label(label="CONTRACT"), False, False, 0)
        self.contract = Gtk.TextView(); self.contract.set_monospace(True); right.pack_start(self.scrolled(self.contract), True, True, 0)
        self.button("Apply Contract", self.apply_contract, parent=right)
        self.detail = Gtk.TextView(); self.detail.set_editable(False); self.detail.set_monospace(True); right.pack_start(self.scrolled(self.detail), True, True, 0)
        outer.set_position(360); middle_right.set_position(590)
        self.refresh_steps()

    def selected_definition(self):
        step_id = self.selected(self.step_tree, 1)
        return self.registry.get(step_id) if step_id else None

    def refresh_steps(self):
        selected = self.selected(self.step_tree, 1); query = self.search.get_text().strip().casefold(); self.step_store.clear()
        for definition in self.registry.steps:
            if query and query not in (definition.name + " " + definition.step_id + " " + definition.description).casefold(): continue
            self.step_store.append((definition.name, definition.step_id))
        self.set_status("%d reusable steps • repository %s" % (len(self.registry.steps), self.registry.repository.name))
        if selected:
            model = self.step_tree.get_model(); iterator = model.get_iter_first()
            while iterator is not None:
                if model.get_value(iterator, 1) == selected: self.step_tree.get_selection().select_iter(iterator); break
                iterator = model.iter_next(iterator)
        if self.selected(self.step_tree, 1) is None and len(self.step_store): self.step_tree.get_selection().select_path(0)
        self.refresh_selected()

    def refresh_selected(self):
        definition = self.selected_definition(); self.flow_store.clear()
        if definition is None:
            self.contract.get_buffer().set_text(""); self.detail.get_buffer().set_text(""); return
        for call in definition.plan.steps:
            self.flow_store.append((call.node_id, call.step_id, json.dumps(call.to_dict().get("inputs", {}), separators=(",", ":"))))
        self.contract.get_buffer().set_text(json.dumps({"name": definition.name, "description": definition.description, "inputs": dict(definition.inputs), "outputs": dict(definition.outputs)}, indent=2, default=str))
        self.detail.get_buffer().set_text("Registry: %s\nObject Repository: %s\nInternal calls: %d" % (self.registry.name, self.registry.repository, len(definition.plan.steps)))

    def show_call(self):
        definition = self.selected_definition(); node_id = self.selected(self.flow_tree, 0)
        if definition is None or not node_id: return
        call = next(item for item in definition.plan.steps if item.node_id == node_id)
        self.detail.get_buffer().set_text(json.dumps(call.to_dict(), indent=2, default=str))

    def new_step(self):
        step_id = self.ask_text("New Registry Step", "Step ID (for example authentication.login):")
        if not step_id: return
        name = self.ask_text("New Registry Step", "Display name:", step_id.rsplit(".", 1)[-1].replace("_", " ").title())
        if not name: return
        try: self.registry.get(step_id); return self.error("New Registry Step", "Step ID already exists: %s" % step_id)
        except Exception: pass
        definition = ReusableStepDefinition(step_id=step_id, name=name, description="", plan=TestPlan(name=name), inputs={}, outputs={})
        self.registry = self.registry.with_step(definition); self.mark_dirty(); self.refresh_steps()

    def duplicate_step(self):
        definition = self.selected_definition()
        if definition is None: return
        step_id = self.ask_text("Duplicate Registry Step", "New step ID:", definition.step_id + ".copy")
        if not step_id: return
        name = self.ask_text("Duplicate Registry Step", "Display name:", definition.name + " Copy")
        if not name: return
        duplicate = replace(definition, step_id=step_id, name=name, plan=replace(definition.plan, name=name))
        self.registry = self.registry.with_step(duplicate); self.mark_dirty(); self.refresh_steps()

    def remove_step(self):
        definition = self.selected_definition()
        if definition and self.confirm("Remove Registry Step", "Remove %s from this Registry?" % definition.name):
            self.registry = self.registry.without_step(definition.step_id); self.mark_dirty(); self.refresh_steps()

    def apply_contract(self):
        definition = self.selected_definition()
        if definition is None: return
        buffer = self.contract.get_buffer(); raw = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        try:
            value = json.loads(raw); inputs = value.get("inputs", {}); outputs = value.get("outputs", {})
            if not isinstance(inputs, dict) or not isinstance(outputs, dict): raise ValueError("inputs and outputs must be JSON objects")
            updated = replace(definition, name=str(value.get("name", definition.name)), description=str(value.get("description", definition.description)), inputs=inputs, outputs=outputs, plan=replace(definition.plan, name=str(value.get("name", definition.name))))
            self.registry = self.registry.with_step(updated)
        except Exception as exc: return self.error("Registry Contract", str(exc))
        self.mark_dirty(); self.refresh_steps()

    def add_call(self):
        definition = self.selected_definition()
        if definition is None: return self.info("Registry Step", "Create or select a Registry Step first.")
        choices = [(item.name, "Primitive") for item in self.runtime_registry.definitions()]
        if self.project_resources:
            for step_id in sorted(self.project_resources.steps):
                if step_id != definition.step_id: choices.append((step_id, "Reusable"))
        dialog = Gtk.Dialog(title="Add Composition Call", transient_for=self.window, modal=True); dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        combo = Gtk.ComboBoxText()
        for step_id, kind in sorted(choices): combo.append(step_id, "%s — %s" % (step_id, kind))
        if choices: combo.set_active(0)
        box.pack_start(Gtk.Label(label="Step"), False, False, 0); box.pack_start(combo, False, False, 0)
        inputs = Gtk.Entry(); inputs.set_text("{}"); outputs = Gtk.Entry(); outputs.set_text("{}")
        box.pack_start(Gtk.Label(label="Inputs JSON"), False, False, 0); box.pack_start(inputs, False, False, 0)
        box.pack_start(Gtk.Label(label="Outputs JSON"), False, False, 0); box.pack_start(outputs, False, False, 0)
        dialog.show_all(); response = dialog.run(); step_id = combo.get_active_id(); raw_inputs = inputs.get_text(); raw_outputs = outputs.get_text(); dialog.destroy()
        if response != Gtk.ResponseType.OK or not step_id: return
        try:
            input_values = json.loads(raw_inputs or "{}"); output_values = json.loads(raw_outputs or "{}")
            if not isinstance(input_values, dict) or not isinstance(output_values, dict): raise ValueError("inputs/outputs must be JSON objects")
            call = StepCall(node_id=_next_node_id(definition.plan.steps), step_id=step_id, inputs=input_values, outputs={str(k): str(v) for k, v in output_values.items()})
            plan = replace(definition.plan, steps=(*definition.plan.steps, call)); self.registry = self.registry.with_step(replace(definition, plan=plan))
        except Exception as exc: return self.error("Add Composition Call", str(exc))
        self.mark_dirty(); self.refresh_selected()

    def edit_call(self):
        definition = self.selected_definition(); node_id = self.selected(self.flow_tree, 0)
        if definition is None or not node_id: return self.info("Registry Step", "Select an internal call first.")
        call = next(item for item in definition.plan.steps if item.node_id == node_id)
        dialog = Gtk.Dialog(title="Edit Registry Call", transient_for=self.window, modal=True); dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Apply", Gtk.ResponseType.OK)
        text = Gtk.TextView(); text.set_monospace(True); text.get_buffer().set_text(json.dumps(call.to_dict(), indent=2, default=str)); scroll = self.scrolled(text); scroll.set_size_request(700, 500); dialog.get_content_area().pack_start(scroll, True, True, 0)
        dialog.show_all(); response = dialog.run(); buffer = text.get_buffer(); raw = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True); dialog.destroy()
        if response != Gtk.ResponseType.OK: return
        try:
            payload = json.loads(raw); updated = replace(call, step_id=str(payload.get("step", call.step_id)), inputs=payload.get("inputs", {}), outputs={str(k): str(v) for k, v in payload.get("outputs", {}).items()}, depends_on=tuple(payload.get("depends_on", call.depends_on)), description=str(payload.get("description", call.description)), group="")
            plan = replace(definition.plan, steps=tuple(updated if item.node_id == node_id else item for item in definition.plan.steps)); self.registry = self.registry.with_step(replace(definition, plan=plan))
        except Exception as exc: return self.error("Registry Call", str(exc))
        self.mark_dirty(); self.refresh_selected()

    def remove_call(self):
        definition = self.selected_definition(); node_id = self.selected(self.flow_tree, 0)
        if definition is None or not node_id: return
        plan = replace(definition.plan, steps=tuple(item for item in definition.plan.steps if item.node_id != node_id)); self.registry = self.registry.with_step(replace(definition, plan=plan)); self.mark_dirty(); self.refresh_selected()

    def save(self):
        try: save_step_registry(self.path, self.registry)
        except Exception as exc: return self.error("Save Step Registry", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(False); self.set_status("Saved Step Registry")


def _next_node_id(steps):
    used = {item.node_id for item in steps}; index = 1
    while "step-%03d" % index in used: index += 1
    return "step-%03d" % index
