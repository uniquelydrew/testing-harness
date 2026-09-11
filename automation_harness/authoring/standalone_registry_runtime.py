"""Standalone Step Registry loading and reusable-step saves in the authoring GUI."""
from __future__ import annotations

from pathlib import Path

from automation_harness.authoring.reusable_extraction import save_plan_selection_to_registry
from automation_harness.authoring.step_registry import load_step_registry_resources
from automation_harness.formats import STEP_REGISTRY_SUFFIX


def _paths(app_instance):
    project_paths = tuple(getattr(getattr(app_instance, "project", None), "step_registries", ()) or ())
    external = tuple(getattr(app_instance, "standalone_step_registries", ()) or ())
    result = []
    for path in (*project_paths, *external):
        resolved = Path(path).resolve()
        if resolved not in result:
            result.append(resolved)
    return tuple(result)


def _reload(app_instance):
    paths = _paths(app_instance)
    resources = load_step_registry_resources(paths) if paths else None
    if resources is None:
        from automation_harness.authoring.registry_runtime import _empty_resources
        resources = _empty_resources()
    app_instance.registry_resources = resources
    app_instance.registry_step_definitions = dict(resources.steps)
    if hasattr(app_instance, "refresh_registry_library"):
        app_instance.refresh_registry_library(rebuild_filter=True)
    return resources


def install(app_module):
    if getattr(app_module, "_standalone_registry_runtime_installed", False):
        return

    original_init = app_module.AuthoringApp.__init__
    original_build = app_module.AuthoringApp._build
    original_save_reusable = app_module.AuthoringApp.save_reusable_step
    original_open_project = app_module.AuthoringApp.open_project_dialog
    original_new_project = app_module.AuthoringApp.new_project_dialog

    def init(self, *args, **kwargs):
        self.standalone_step_registries = ()
        original_init(self, *args, **kwargs)

    def build(self):
        original_build(self)
        if self.mode != "author":
            return
        outer = self.window.get_child()
        children = outer.get_children() if outer is not None else []
        if children:
            toolbar = children[0]
            self._button(toolbar, "Open Step Registry", self.open_step_registry_dialog)

    def open_step_registry_dialog(self):
        path = self._choose_file(
            yaml=True,
            artifact_suffix=STEP_REGISTRY_SUFFIX,
            title="Open Step Registry",
        )
        if not path:
            return
        resolved = Path(path).resolve()
        previous = tuple(self.standalone_step_registries)
        if resolved not in previous:
            self.standalone_step_registries = (*previous, resolved)
        try:
            resources = _reload(self)
        except Exception as exc:
            self.standalone_step_registries = previous
            return self._error("Open Step Registry", "%s: %s" % (type(exc).__name__, exc))
        self._set_status(
            "Opened Step Registry: %s (%d reusable steps loaded)" %
            (resolved, len(resources.steps))
        )
        if hasattr(self, "notebook") and hasattr(self, "registry_tree"):
            # Step Library is inserted at index 2 by registry_runtime.
            self.notebook.set_current_page(2)

    def _choose_standalone_registry(self):
        paths = tuple(self.standalone_step_registries)
        if not paths:
            return None
        if len(paths) == 1:
            return paths[0]
        prompt = "Loaded standalone registries:\n" + "\n".join(str(path) for path in paths)
        selected = self._ask_text("Save Step to Registry", prompt + "\n\nTarget path:", str(paths[0]))
        if not selected:
            return None
        resolved = Path(selected).resolve()
        return resolved if resolved in paths else None

    def save_reusable_step(self):
        if self.project is not None:
            return original_save_reusable(self)
        self.refresh_plan()
        if not self.plan.steps:
            return self._info("Reusable step", "Add one or more actions to the test first.")
        registry_path = self._choose_standalone_registry()
        if registry_path is None:
            return self._info(
                "Reusable step",
                "Open a Step Registry or Project before saving a reusable composition.",
            )
        node_id = self._selected(self.plan_tree, 1)
        if not node_id:
            return self._info("Reusable step", "Select an action in the Test Flow first.")
        selected_call = next(item for item in self.plan.steps if item.node_id == node_id)
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
            registry = save_plan_selection_to_registry(
                self.plan,
                source_repository=self.repository,
                registry_path=registry_path,
                step_id=step_id.strip(),
                name=name.strip(),
                **kwargs
            )
            _reload(self)
        except Exception as exc:
            return self._error("Save Step to Registry", "%s: %s" % (type(exc).__name__, exc))
        self._set_status("Saved reusable step %s to %s" % (name, registry.name))

    def open_project_dialog(self):
        result = original_open_project(self)
        try:
            _reload(self)
        except Exception as exc:
            self._error("Step Registries", "%s: %s" % (type(exc).__name__, exc))
        return result

    def new_project_dialog(self):
        result = original_new_project(self)
        try:
            _reload(self)
        except Exception as exc:
            self._error("Step Registries", "%s: %s" % (type(exc).__name__, exc))
        return result

    app_module.AuthoringApp.__init__ = init
    app_module.AuthoringApp._build = build
    app_module.AuthoringApp.open_step_registry_dialog = open_step_registry_dialog
    app_module.AuthoringApp._choose_standalone_registry = _choose_standalone_registry
    app_module.AuthoringApp.save_reusable_step = save_reusable_step
    app_module.AuthoringApp.open_project_dialog = open_project_dialog
    app_module.AuthoringApp.new_project_dialog = new_project_dialog
    app_module._standalone_registry_runtime_installed = True
