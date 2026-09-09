"""Step Registry authoring integration for the GTK authoring surface."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from automation_harness.authoring.project import save_authoring_project
from automation_harness.authoring.project_registry_service import save_plan_selection_to_project_registry
from automation_harness.authoring.step_registry import (
    LoadedStepRegistryResources,
    load_step_registry_resources,
)
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_step_expansion import expand_reusable_steps
from automation_harness.models.plan import PlanVariableRef, StepCall


def _empty_resources():
    return LoadedStepRegistryResources((), {}, ComponentRepository({}))


def _load_project_resources(app_instance):
    project = getattr(app_instance, "project", None)
    if project is None or not project.step_registries:
        resources = _empty_resources()
    else:
        resources = load_step_registry_resources(project.step_registries)
    app_instance.registry_resources = resources
    app_instance.registry_step_definitions = dict(resources.steps)
    return resources


def _effective_repository(app_instance):
    """Merge active-plan objects with Registry-owned objects without hiding conflicts."""
    active = app_instance.repository
    registry = getattr(app_instance, "registry_resources", _empty_resources()).repository
    if not registry.components:
        return active
    merged = dict(registry.components)
    object_ids = {definition.object_id: name for name, definition in merged.items()}
    for name, definition in active.components.items():
        existing = merged.get(name)
        if existing is not None and existing != definition:
            raise ValueError("component %r conflicts between active and Step Registry repositories" % name)
        previous = object_ids.get(definition.object_id)
        if previous is not None and previous != name:
            raise ValueError(
                "immutable object id %r is assigned to both %r and %r" %
                (definition.object_id, previous, name)
            )
        merged[name] = definition
        object_ids[definition.object_id] = name
    return ComponentRepository(merged)


def _expanded_plan(app_instance):
    definitions = getattr(app_instance, "registry_step_definitions", {})
    if not definitions:
        return app_instance.plan
    return expand_reusable_steps(app_instance.plan, definitions)


def _definition_contract_text(definition):
    payload = {
        "id": definition.step_id,
        "name": definition.name,
        "description": definition.description,
        "inputs": dict(definition.inputs),
        "outputs": dict(definition.outputs),
        "actions": len(definition.plan.steps),
    }
    return json.dumps(payload, indent=2, default=str)


def install(app_module):
    """Install Project-scoped reusable Step Registry authoring behavior."""
    if getattr(app_module, "_authoring_registry_runtime_installed", False):
        return

    Gtk = app_module.Gtk
    original_init = app_module.AuthoringApp.__init__
    original_build = app_module.AuthoringApp._build
    original_new_project = app_module.AuthoringApp.new_project_dialog
    original_open_project = app_module.AuthoringApp.open_project_dialog
    original_save_plan = app_module.AuthoringApp.save_plan_dialog
    original_run = app_module.AuthoringApp.run_reference_plan
    original_refresh_state = app_module.AuthoringApp.refresh_state

    def init(self, *args, **kwargs):
        self.registry_resources = _empty_resources()
        self.registry_step_definitions = {}
        original_init(self, *args, **kwargs)
        try:
            _load_project_resources(self)
            self.refresh_registry_library()
        except Exception as exc:
            self._error("Step Registries", "%s: %s" % (type(exc).__name__, exc))

    def build(self):
        original_build(self)
        if self.mode != "author":
            return
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.set_border_width(6)

        filters = Gtk.Box(spacing=6)
        page.pack_start(filters, False, False, 0)
        filters.pack_start(Gtk.Label(label="Registry:"), False, False, 0)
        self.registry_filter = Gtk.ComboBoxText()
        self.registry_filter.append("__all__", "All Registries")
        self.registry_filter.set_active_id("__all__")
        self.registry_filter.connect("changed", lambda *_args: self.refresh_registry_library())
        filters.pack_start(self.registry_filter, False, False, 0)
        filters.pack_start(Gtk.Label(label="Search:"), False, False, 0)
        self.registry_search = Gtk.SearchEntry()
        self.registry_search.set_placeholder_text("Step name, ID, description, input or output")
        self.registry_search.connect("search-changed", lambda *_args: self.refresh_registry_library())
        filters.pack_start(self.registry_search, True, True, 0)
        self.registry_count = Gtk.Label(label="0 steps")
        filters.pack_end(self.registry_count, False, False, 0)

        body = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        page.pack_start(body, True, True, 0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.pack1(left, resize=True, shrink=False)
        self.registry_tree, self.registry_store = self._tree((
            ("Registry", 180), ("Step", 200), ("ID", 230), ("Description", 360),
        ))
        self.registry_tree.get_selection().connect("changed", lambda *_args: self.show_registry_step())
        self.registry_tree.connect("row-activated", lambda *_args: self.insert_registry_step())
        left.pack_start(self._scrolled(self.registry_tree), True, True, 0)
        buttons = Gtk.Box(spacing=6)
        left.pack_start(buttons, False, False, 0)
        self._button(buttons, "Insert Selected", self.insert_registry_step)
        self._button(buttons, "Reload Registries", self.reload_registry_resources)

        self.registry_detail = Gtk.TextView()
        self.registry_detail.set_editable(False)
        self.registry_detail.set_monospace(True)
        body.pack2(self._scrolled(self.registry_detail), resize=True, shrink=False)
        body.set_position(780)
        self.notebook.insert_page(page, Gtk.Label(label="Step Library"), 2)
        page.show_all()

    def reload_registry_resources(self):
        try:
            _load_project_resources(self)
            self.refresh_registry_library(rebuild_filter=True)
            self._set_status("Loaded %d reusable step(s)" % len(self.registry_step_definitions))
        except Exception as exc:
            self._error("Step Registries", "%s: %s" % (type(exc).__name__, exc))

    def refresh_registry_library(self, rebuild_filter=False):
        if not hasattr(self, "registry_store"):
            return
        selected = self._selected(self.registry_tree, 2)
        resources = getattr(self, "registry_resources", _empty_resources())
        if rebuild_filter:
            active = self.registry_filter.get_active_id() or "__all__"
            self.registry_filter.remove_all()
            self.registry_filter.append("__all__", "All Registries")
            available = {"__all__"}
            for index, registry in enumerate(resources.registries):
                key = str(index)
                self.registry_filter.append(key, registry.name)
                available.add(key)
            self.registry_filter.set_active_id(active if active in available else "__all__")

        self.registry_store.clear()
        query = self.registry_search.get_text().strip().casefold() if hasattr(self, "registry_search") else ""
        active = self.registry_filter.get_active_id() if hasattr(self, "registry_filter") else "__all__"
        visible = 0
        total = 0
        for index, registry in enumerate(resources.registries):
            if active not in (None, "__all__", str(index)):
                continue
            for definition in registry.steps:
                total += 1
                searchable = " ".join((
                    registry.name,
                    definition.name,
                    definition.step_id,
                    definition.description,
                    " ".join(definition.inputs),
                    " ".join(definition.outputs),
                )).casefold()
                if query and query not in searchable:
                    continue
                self.registry_store.append((
                    registry.name,
                    definition.name,
                    definition.step_id,
                    definition.description,
                ))
                visible += 1
        self.registry_count.set_text(
            "%d of %d steps" % (visible, total) if query else "%d steps" % visible
        )
        self._select_value(self.registry_tree, selected, 2)

    def show_registry_step(self):
        step_id = self._selected(self.registry_tree, 2)
        if not step_id:
            self._set_text(self.registry_detail, "")
            return
        definition = self.registry_step_definitions.get(step_id)
        if definition is not None:
            self._set_text(self.registry_detail, _definition_contract_text(definition))

    def configure_registry_step(self, definition):
        dialog = Gtk.Dialog(
            title="Insert %s" % definition.name,
            transient_for=self.window,
            modal=True,
        )
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Insert", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        note = Gtk.Label(label=definition.description or definition.step_id)
        note.set_xalign(0); box.pack_start(note, False, False, 0)
        input_entries = {}
        for name, metadata in definition.inputs.items():
            row = Gtk.Box(spacing=8)
            required = bool(metadata.get("required", False))
            label = Gtk.Label(label="%s%s (%s)" % (name, " *" if required else "", metadata.get("type", "any")))
            label.set_xalign(0); label.set_size_request(220, -1)
            entry = Gtk.Entry()
            if "default" in metadata:
                entry.set_text(json.dumps(metadata["default"], default=str))
            entry.set_placeholder_text("JSON value or ${variable}")
            row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0)
            box.pack_start(row, False, False, 0)
            input_entries[name] = (metadata, entry)
        output_entries = {}
        for name in definition.outputs:
            row = Gtk.Box(spacing=8)
            label = Gtk.Label(label="Output %s" % name); label.set_xalign(0); label.set_size_request(220, -1)
            entry = Gtk.Entry(); entry.set_text(name)
            entry.set_placeholder_text("test variable name; blank to ignore")
            row.pack_start(label, False, False, 0); row.pack_start(entry, True, True, 0)
            box.pack_start(row, False, False, 0)
            output_entries[name] = entry
        error = Gtk.Label(); error.set_xalign(0); box.pack_start(error, False, False, 0)
        dialog.show_all()
        while True:
            response = dialog.run()
            if response != Gtk.ResponseType.OK:
                dialog.destroy(); return None
            inputs = {}; missing = []
            for name, (metadata, entry) in input_entries.items():
                raw = entry.get_text().strip()
                if not raw:
                    if metadata.get("required", False) and "default" not in metadata:
                        missing.append(name)
                    continue
                if raw.startswith("${") and raw.endswith("}"):
                    inputs[name] = PlanVariableRef(raw[2:-1].strip())
                else:
                    try: inputs[name] = json.loads(raw)
                    except ValueError: inputs[name] = raw
            if missing:
                error.set_text("Required inputs: " + ", ".join(missing)); continue
            outputs = {
                name: entry.get_text().strip()
                for name, entry in output_entries.items()
                if entry.get_text().strip()
            }
            dialog.destroy(); return inputs, outputs

    def insert_registry_step(self):
        step_id = self._selected(self.registry_tree, 2)
        if not step_id:
            return self._info("Step Library", "Select a reusable step first.")
        definition = self.registry_step_definitions.get(step_id)
        if definition is None:
            return self._error("Step Library", "Reusable step %r is no longer loaded." % step_id)
        configured = self.configure_registry_step(definition)
        if configured is None:
            return
        inputs, outputs = configured
        call = StepCall(
            node_id=app_module._next_node_id(self.plan.steps),
            step_id=definition.step_id,
            inputs=inputs,
            outputs=outputs,
            group=self._current_step_group(),
        )
        self.plan = replace(self.plan, steps=(*self.plan.steps, call))
        self.refresh_plan(); self.refresh_state()
        self._set_status("Inserted reusable step %s" % definition.name)

    def choose_registry_for_save(self):
        project = self.project
        if project is None:
            return None
        dialog = Gtk.Dialog(title="Save Step to Registry", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Continue", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10)
        combo = Gtk.ComboBoxText()
        for path in project.step_registries:
            combo.append(str(path), path.stem)
        combo.append("__new__", "+ Create New Registry")
        combo.set_active(0 if project.step_registries else 0)
        box.pack_start(Gtk.Label(label="Target Step Registry"), False, False, 0)
        box.pack_start(combo, False, False, 0)
        dialog.show_all(); response = dialog.run(); selected = combo.get_active_id(); dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return None
        if selected != "__new__":
            return Path(selected), None
        path = self._choose_file(
            save=True,
            yaml=True,
            artifact_suffix=app_module.STEP_REGISTRY_SUFFIX,
            title="Create Step Registry",
        )
        if not path:
            return None
        registry_name = self._ask_text("New Step Registry", "Registry name:", Path(path).stem)
        if not registry_name:
            return None
        return Path(path), registry_name

    def save_reusable_step(self):
        self.refresh_plan()
        if not self.plan.steps:
            return self._info("Reusable step", "Add one or more actions to the test first.")
        if self.project is None or self.project_path is None:
            return self._info("Reusable step", "Open a Project before saving a plan composition to a Registry.")
        node_id = self._selected(self.plan_tree, 1)
        if not node_id:
            return self._info("Reusable step", "Select an action in the Test Flow first.")
        selected_call = next(item for item in self.plan.steps if item.node_id == node_id)
        selection = self.choose_registry_for_save()
        if selection is None:
            return
        registry_path, new_registry_name = selection
        step_id = self._ask_text(
            "Save Step to Registry",
            "Reusable step ID (for example authentication.login):",
        )
        if not step_id:
            return
        name = self._ask_text(
            "Save Step to Registry",
            "Display name:",
            selected_call.group or selected_call.step_id,
        )
        if not name:
            return
        kwargs = {"group": selected_call.group} if selected_call.group else {"node_ids": (selected_call.node_id,)}
        try:
            project, registry = save_plan_selection_to_project_registry(
                Path(self.project_path),
                self.plan,
                source_repository=self.repository,
                registry_path=registry_path,
                step_id=step_id.strip(),
                name=name.strip(),
                create_registry_name=new_registry_name,
                **kwargs
            )
            self.project = project
            _load_project_resources(self)
            self.refresh_registry_library(rebuild_filter=True)
        except Exception as exc:
            return self._error("Save Step to Registry", "%s: %s" % (type(exc).__name__, exc))
        self._set_status("Saved reusable step %s to %s" % (name, registry.name))

    def new_project_dialog(self):
        result = original_new_project(self)
        try:
            _load_project_resources(self); self.refresh_registry_library(rebuild_filter=True)
        except Exception as exc:
            self._error("Step Registries", "%s: %s" % (type(exc).__name__, exc))
        return result

    def open_project_dialog(self):
        result = original_open_project(self)
        try:
            _load_project_resources(self); self.refresh_registry_library(rebuild_filter=True)
        except Exception as exc:
            self._error("Step Registries", "%s: %s" % (type(exc).__name__, exc))
        return result

    def save_plan_dialog(self):
        result = original_save_plan(self)
        if self.project is not None and self.project_path is not None and self.plan_path is not None:
            try:
                updated = self.project.with_test_plan(Path(self.plan_path))
                save_authoring_project(Path(self.project_path), updated)
                self.project = updated
            except Exception as exc:
                self._error("Project", "Plan saved, but Project membership could not be updated: %s" % exc)
        return result

    def refresh_state(self):
        definitions = getattr(self, "registry_step_definitions", {})
        if not definitions:
            return original_refresh_state(self)
        authored = self.plan
        try:
            self.plan = _expanded_plan(self)
            result = original_refresh_state(self)
            self.state_caption.set_text("Pre-execution projection (Registry Steps expanded)")
            return result
        except Exception as exc:
            self.state_store.clear()
            self.state_caption.set_text("Registry expansion error: %s" % exc)
        finally:
            self.plan = authored
            self.refresh_plan()

    def run_reference_plan(self):
        authored_plan = self.plan
        authored_repository = self.repository
        try:
            self.plan = _expanded_plan(self)
            self.repository = _effective_repository(self)
            return original_run(self)
        except Exception as exc:
            return self._error("Run Test", "%s: %s" % (type(exc).__name__, exc))
        finally:
            self.plan = authored_plan
            self.repository = authored_repository
            self.refresh_plan()

    app_module.AuthoringApp.__init__ = init
    app_module.AuthoringApp._build = build
    app_module.AuthoringApp.reload_registry_resources = reload_registry_resources
    app_module.AuthoringApp.refresh_registry_library = refresh_registry_library
    app_module.AuthoringApp.show_registry_step = show_registry_step
    app_module.AuthoringApp.configure_registry_step = configure_registry_step
    app_module.AuthoringApp.insert_registry_step = insert_registry_step
    app_module.AuthoringApp.choose_registry_for_save = choose_registry_for_save
    app_module.AuthoringApp.save_reusable_step = save_reusable_step
    app_module.AuthoringApp.new_project_dialog = new_project_dialog
    app_module.AuthoringApp.open_project_dialog = open_project_dialog
    app_module.AuthoringApp.save_plan_dialog = save_plan_dialog
    app_module.AuthoringApp.refresh_state = refresh_state
    app_module.AuthoringApp.run_reference_plan = run_reference_plan
    app_module._authoring_registry_runtime_installed = True
