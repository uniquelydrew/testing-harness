from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk

from automation_harness.authoring.gui.common import ArtifactWindow
from automation_harness.authoring.plan_repository import assigned_repository_path
from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.authoring.step_registry import AuthoringStepRegistry, create_step_registry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_plan import load_plan, repository_from_plan, save_plan
from automation_harness.formats import PLAN_SUFFIX, REPOSITORY_SUFFIX, STEP_REGISTRY_SUFFIX
from automation_harness.models.plan import TestPlan


class ProjectWindow(ArtifactWindow):
    title_prefix = "Automation Harness Project"

    def __init__(self, path, *, opener=None):
        super().__init__(path, project_context=path, opener=opener)
        self.project = AuthoringProject.load(self.path)
        self.button("Save Project", self.save)
        self.button("Add Existing", self.add_existing)
        self.button("+ New Artifact", self.new_artifact)

        paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.root.pack_start(paned, True, True, 0)
        self.tree, self.store = self.list_tree((("Type", 170), ("Artifact", 300), ("Path", 520)))
        self.tree.connect("row-activated", lambda *_args: self.open_selected())
        self.tree.connect("button-press-event", self._tree_button_press)
        self.tree.connect("key-press-event", self._tree_key_press)
        paned.pack1(self.scrolled(self.tree), resize=True, shrink=False)
        self.detail = Gtk.TextView(); self.detail.set_editable(False); self.detail.set_monospace(True)
        paned.pack2(self.scrolled(self.detail), resize=True, shrink=False)
        paned.set_position(720)
        self.tree.get_selection().connect("changed", lambda *_args: self.refresh_detail())
        self.refresh()

    def refresh(self):
        self.store.clear()
        groups = (
            ("Test Plan", self.project.test_plans),
            ("Step Registry", self.project.step_registries),
            ("Object Repository", self.project.object_repositories),
        )
        for label, paths in groups:
            for path in paths:
                self.store.append((label, path.stem, str(path)))
        self.set_status(
            "%d plans • %d registries • %d repositories" %
            (len(self.project.test_plans), len(self.project.step_registries), len(self.project.object_repositories))
        )
        self.refresh_detail()

    def refresh_detail(self):
        path_text = self.selected(self.tree, 2)
        label = self.selected(self.tree, 0)
        if not path_text:
            text = (
                "%s\n\nTest Plans: %d\nStep Registries: %d\nObject Repositories: %d" %
                (self.project.name, len(self.project.test_plans), len(self.project.step_registries), len(self.project.object_repositories))
            )
            self.detail.get_buffer().set_text(text)
            return

        artifact = Path(path_text)
        if not artifact.is_file():
            self.detail.get_buffer().set_text("%s\n\nThe artifact file is missing." % artifact.stem)
            return

        lines = [label.upper(), "", artifact.stem]
        try:
            if label == "Object Repository":
                repository = ComponentRepository.load((artifact,))
                type_counts = {}
                for definition in repository.components.values():
                    object_type = definition.object_type.value
                    type_counts[object_type] = type_counts.get(object_type, 0) + 1
                lines.append("%d objects" % len(repository.components))
                if type_counts:
                    lines.append("Types: %s" % ", ".join("%s (%d)" % item for item in sorted(type_counts.items())))

            elif label == "Test Plan":
                plan = load_plan(artifact)
                assigned = assigned_repository_path(plan, artifact)
                lines.extend((
                    "%d steps · %d variables" % (len(plan.steps), len(plan.variables)),
                    "Object repository: %s" % (assigned.stem if assigned is not None else "embedded"),
                ))

            elif label == "Step Registry":
                registry = AuthoringStepRegistry.load(artifact)
                lines.extend((
                    "%d reusable steps" % len(registry.steps),
                    "Object repository: %s" % Path(registry.repository).stem,
                ))
        except Exception as exc:
            lines.extend(("", "Unable to inspect artifact:", "%s: %s" % (type(exc).__name__, exc)))

        self.detail.get_buffer().set_text("\n".join(lines))

    def _tree_button_press(self, _tree, event):
        if event.button != 3:
            return False
        path_info = self.tree.get_path_at_pos(int(event.x), int(event.y))
        if path_info is None:
            return False
        path, _column, _cell_x, _cell_y = path_info
        self.tree.get_selection().select_path(path)
        menu = Gtk.Menu()
        open_item = Gtk.MenuItem(label="Open")
        open_item.connect("activate", lambda *_args: self.open_selected())
        menu.append(open_item)
        remove_item = Gtk.MenuItem(label="Remove from Project")
        remove_item.connect("activate", lambda *_args: self.remove_selected())
        menu.append(remove_item)
        menu.show_all()
        menu.popup_at_pointer(None)
        return True

    def _tree_key_press(self, _tree, event):
        if event.keyval == Gdk.KEY_Delete:
            self.remove_selected()
            return True
        return False

    def save(self):
        save_authoring_project(self.path, self.project)
        self.mark_dirty(False); self.set_status("Saved project")

    def open_selected(self):
        path = self.selected(self.tree, 2)
        if path:
            return self.open_artifact(Path(path), project_context=self.path)

    def remove_selected(self):
        path_text = self.selected(self.tree, 2)
        label = self.selected(self.tree, 0)
        if not path_text:
            return self.info("Project", "Select an artifact first.")
        path = Path(path_text)
        if not self.confirm("Remove from Project", "Remove %s from Project membership? The file will not be deleted." % path.name):
            return
        if label == "Test Plan": self.project = self.project.without_test_plan(path)
        elif label == "Step Registry": self.project = self.project.without_step_registry(path)
        else: self.project = self.project.without_object_repository(path)
        self.mark_dirty(); self.refresh()

    def add_existing(self):
        path = self.choose_file(title="Add existing artifact")
        if path is None:
            return
        from automation_harness.authoring.gui.router import ArtifactType, detect_artifact
        try:
            kind = detect_artifact(path)
            if kind is ArtifactType.TEST_PLAN: self.project = self.project.with_test_plan(path)
            elif kind is ArtifactType.STEP_REGISTRY:
                registry = AuthoringStepRegistry.load(path)
                self.project = self.project.with_step_registry(path).with_object_repository(registry.repository)
            elif kind is ArtifactType.OBJECT_REPOSITORY: self.project = self.project.with_object_repository(path)
            else: return self.error("Project", "A Project cannot be added as a child artifact.")
        except Exception as exc:
            return self.error("Project", "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(); self.refresh()

    def new_artifact(self):
        dialog = Gtk.Dialog(title="New Project Artifact", transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Create", Gtk.ResponseType.OK)
        combo = Gtk.ComboBoxText(); combo.append("plan", "Test Plan"); combo.append("registry", "Step Registry"); combo.append("repository", "Object Repository"); combo.set_active(0)
        box = dialog.get_content_area(); box.set_spacing(8); box.set_border_width(10); box.pack_start(combo, False, False, 0)
        dialog.show_all(); response = dialog.run(); kind = combo.get_active_id(); dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return
        if kind == "plan": suffix = PLAN_SUFFIX; title = "Create Test Plan"
        elif kind == "registry": suffix = STEP_REGISTRY_SUFFIX; title = "Create Step Registry"
        else: suffix = REPOSITORY_SUFFIX; title = "Create Object Repository"
        path = self.choose_file(title=title, save=True, suffix=suffix)
        if path is None:
            return
        name = self.ask_text(title, "Name:", path.stem)
        if not name:
            return
        try:
            if kind == "plan":
                save_plan(TestPlan(name=name), path); self.project = self.project.with_test_plan(path)
            elif kind == "registry":
                registry = create_step_registry(path, name); self.project = self.project.with_step_registry(path).with_object_repository(registry.repository)
            else:
                ComponentRepository({}).save(path); self.project = self.project.with_object_repository(path)
        except Exception as exc:
            return self.error(title, "%s: %s" % (type(exc).__name__, exc))
        self.mark_dirty(); self.save(); self.refresh(); self.open_artifact(path, project_context=self.path)
