"""Test Plan visual matching and action authoring; execution is inherited."""
from __future__ import annotations

from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.steps import visual_assert_steps as _visual_assert_steps  # noqa: F401
from automation_harness.authoring.action_catalog import action_by_id, actions_for
from automation_harness.authoring.step_naming import default_step_name
from automation_harness.authoring.gui.action_widgets import create_action_input_widget, read_action_input
from automation_harness.authoring.step_order import insert_calls
from automation_harness.authoring.gui.plan_recording_window import RecordingTestPlanWindow
from automation_harness.authoring.gui.plan_window import _next_node_id
from automation_harness.authoring.visual_match_assets import import_visual_match
from automation_harness.core.test_plan import validate_plan_components
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.plan import StepCall
from automation_harness.models.gui import GuiAction
from automation_harness.extensions.runtime import load_registry_from_environment


class VisualTestPlanWindow(RecordingTestPlanWindow):
    """Recording-capable Test Plan editor with explicit repository visual assertions."""

    def add_object_action(self, component_id=None, action_id=None):
        """Author a typed action on one canonical repository object.

        Keep the action editor and visual assertion controls alive across
        action changes. GTK's changed callbacks must rebuild parameter fields
        without destroying widgets later reused by the Assert Match option.
        """
        try:
            extension_registry = load_registry_from_environment()
        except (OSError, ValueError, KeyError) as exc:
            return self.error("Extension Configuration", "%s: %s" % (type(exc).__name__, exc))
        self.refresh_objects()
        if not self.repository.components:
            return self.info(
                "Object Action",
                "No objects are available. Open or capture objects in an Object Repository first.",
            )
        dialog = Gtk.Dialog(title="Add Object Action", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Add", Gtk.ResponseType.OK)
        dialog.set_default_size(480, -1)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)

        object_combo = Gtk.ComboBoxText()
        for known_id in sorted(self.repository.components):
            object_combo.append(known_id, known_id)
        if component_id and self.repository.contains(component_id):
            object_combo.set_active_id(self.repository.get(component_id).component_id)
        else:
            object_combo.set_active(0)
        action_combo = Gtk.ComboBoxText()

        # Never destroy the assertion controls during an action change.
        inputs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        action_fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        visual_fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        visual_fields.set_no_show_all(True)
        inputs_box.pack_start(action_fields, False, False, 0)
        inputs_box.pack_start(visual_fields, False, False, 0)
        input_entries = {}

        visual_combo = Gtk.ComboBoxText()
        add_match_button = Gtk.Button(label="Add New Match Image…")
        match_percentage = Gtk.SpinButton.new_with_range(0.0, 100.0, 0.1)
        match_percentage.set_digits(1)
        for label, control in (
            ("Match Image", visual_combo),
            ("Minimum Match (%)", match_percentage),
        ):
            field = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            caption = Gtk.Label(label=label)
            caption.set_size_request(170, -1)
            caption.set_xalign(0)
            field.pack_start(caption, False, False, 0)
            field.pack_start(control, True, True, 0)
            visual_fields.pack_start(field, False, False, 0)
        visual_fields.pack_start(add_match_button, False, False, 0)
        # no_show_all prevents the parent dialog from unexpectedly revealing
        # the visual controls for ordinary actions. Show the children directly
        # so later toggling the visual section also reveals its contents.
        for visual_child in visual_fields.get_children():
            visual_child.show_all()

        def selected_definition():
            selected_id = object_combo.get_active_id()
            return self.repository.get(selected_id) if selected_id else None

        def current_variant():
            definition = selected_definition()
            variant_id = visual_combo.get_active_id()
            if definition is None or not variant_id:
                return None
            return (definition.visual or {}).get("variants", {}).get(variant_id)

        def reset_match_percentage(*_args):
            variant = current_variant()
            ratio = 0.01 if variant is None else float(variant.get("max_difference_ratio", 0.01))
            match_percentage.set_value(max(0.0, min(100.0, (1.0 - ratio) * 100.0)))

        def rebuild_visuals():
            visual_combo.remove_all()
            definition = selected_definition()
            variants = (definition.visual or {}).get("variants", {}) if definition else {}
            for key, variant in sorted(variants.items()):
                visual_combo.append(key, "%s — %s" % (key, variant.get("image", "")))
            if variants:
                visual_combo.set_active(0)
            reset_match_percentage()

        def add_match(*_args):
            definition = selected_definition()
            if definition is None:
                return
            image = self.choose_file(title="Select Match Image")
            if image is None:
                return
            match_id = self.ask_text("Add Match Image", "Match image ID:", image.stem)
            if not match_id:
                return
            repository_path = definition.repository_path
            if repository_path is None and self.project is not None:
                for candidate in self.project.object_repositories:
                    candidate_repo = ComponentRepository.load([candidate])
                    if candidate_repo.contains(definition.component_id):
                        repository_path = candidate
                        break
            if repository_path is None:
                return self.error(
                    "Assert Match",
                    "The selected object has no writable Object Repository. "
                    "Assign its repository to the Test Plan first.",
                )
            try:
                updated = import_visual_match(
                    repository_path, definition.component_id, image, match_id,
                )
                self.repository = self.repository.with_component(updated)
            except Exception as exc:
                return self.error("Assert Match", "%s: %s" % (type(exc).__name__, exc))
            rebuild_visuals()
            visual_combo.set_active_id(match_id)
            reset_match_percentage()
            self.mark_dirty()

        def rebuild_inputs(*_args):
            for child in action_fields.get_children():
                child.destroy()
            input_entries.clear()
            definition = selected_definition()
            selected_action = action_combo.get_active_id()
            visual_fields.set_visible(selected_action == "assert_match")
            if definition is None or not selected_action:
                return
            if selected_action == "assert_match":
                return
            action = action_by_id(definition, selected_action, registry=extension_registry)
            if not action.inputs:
                caption = Gtk.Label(label="No parameters required for this action.")
                caption.set_xalign(0)
                action_fields.pack_start(caption, False, False, 0)
            for item in action.inputs:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
                caption = Gtk.Label(label=item.name + (" *" if item.required else ""))
                caption.set_size_request(170, -1)
                caption.set_xalign(0)
                entry = create_action_input_widget(item, definition)
                row.pack_start(caption, False, False, 0)
                row.pack_start(entry, True, True, 0)
                action_fields.pack_start(row, False, False, 0)
                input_entries[item.name] = (item, entry)
            action_fields.show_all()

        preferred_action_id = [action_id]

        def rebuild_actions(*_args):
            previous = action_combo.get_active_id()
            action_combo.remove_all()
            definition = selected_definition()
            if definition is None:
                rebuild_inputs()
                return
            try:
                definitions = actions_for(definition, registry=extension_registry)
            except (ValueError, KeyError) as exc:
                self.error("Extension Configuration", "%s: %s" % (type(exc).__name__, exc))
                rebuild_inputs()
                return
            for action in definitions:
                action_combo.append(action.action_id, "%s — %s" % (action.name, action.category))
            action_combo.append("assert_match", "Assert Match — Assertion")
            available = {item.action_id for item in definitions} | {"assert_match"}
            desired = preferred_action_id[0] or previous
            preferred_action_id[0] = None
            if desired in available:
                action_combo.set_active_id(desired)
            else:
                action_combo.set_active(0)
            rebuild_visuals()

        object_combo.connect("changed", rebuild_actions)
        action_combo.connect("changed", rebuild_inputs)
        visual_combo.connect("changed", reset_match_percentage)
        add_match_button.connect("clicked", add_match)
        for label, control in (("Object", object_combo), ("Action", action_combo)):
            caption = Gtk.Label(label=label)
            caption.set_xalign(0)
            box.pack_start(caption, False, False, 0)
            box.pack_start(control, False, False, 0)
        action_inputs_label = Gtk.Label(label="Action Inputs")
        action_inputs_label.set_xalign(0)
        box.pack_start(action_inputs_label, False, False, 0)
        box.pack_start(inputs_box, False, False, 0)
        group_entry = Gtk.Entry()
        group_entry.set_placeholder_text("Composed step/group")
        group_caption = Gtk.Label(label="Composed Step")
        group_caption.set_xalign(0)
        box.pack_start(group_caption, False, False, 0)
        box.pack_start(group_entry, False, False, 0)
        rebuild_actions()
        rebuild_inputs()
        dialog.show_all()
        visual_fields.set_visible(action_combo.get_active_id() == "assert_match")

        added = None
        added_description = None
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                break
            selected_id = object_combo.get_active_id()
            selected_action = action_combo.get_active_id()
            try:
                if not selected_id or not self.repository.contains(selected_id):
                    raise ValueError("Select an Object.")
                if not selected_action:
                    raise ValueError("Select an Action.")
                component = self.repository.get(selected_id)
                canonical_id = component.object_id
                group = group_entry.get_text().strip() or "Step"
                node_id = _next_node_id(self.plan.steps)
                if selected_action == "assert_match":
                    variant_key = visual_combo.get_active_id()
                    if not variant_key:
                        raise ValueError("Select an existing match image or add a new one.")
                    call = StepCall(
                        node_id,
                        "gui.object.visual.assert",
                        inputs={
                            "component_id": canonical_id,
                            "variant_key": variant_key,
                            "minimum_match_percentage": float(match_percentage.get_value()),
                        },
                        group=group,
                        name=default_step_name("Assert Match", component.component_id, component.object_type),
                    )
                    description = "Assert Match"
                else:
                    action = action_by_id(component, selected_action, registry=extension_registry)
                    values = {}
                    for name, (item, entry) in input_entries.items():
                        present, value = read_action_input(item, entry)
                        if not present:
                            if item.required:
                                raise ValueError("Missing required Action Input: %s" % name)
                            if item.default is not None:
                                values[name] = item.default
                            continue
                        values[name] = value
                    call = replace(action.to_step_call(
                        node_id, canonical_id, values,
                        object_label=component.component_id, object_type=component.object_type,
                    ), group=group)
                    if call.step_id == "gui.object.action":
                        GuiAction.from_value(call.inputs["action"])
                    description = action.name
                candidate = insert_calls(
                    self.plan, (call,), after_node_id=self.selected(self.flow_tree, 1),
                )
                issues = validate_plan_components(
                    replace(self.plan, steps=(call,)), self.repository,
                )
                if issues:
                    raise ValueError("; ".join(issues))
            except Exception as exc:
                self.error("Object Action", "%s: %s" % (type(exc).__name__, exc))
                continue
            self.plan = candidate
            added = component.component_id
            added_description = description
            break
        dialog.destroy()
        if added is None:
            return
        self.mark_dirty()
        self.refresh_all()
        self.set_status("Added %s on %s" % (added_description, added))

