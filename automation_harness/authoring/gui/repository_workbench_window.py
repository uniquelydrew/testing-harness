from __future__ import annotations

from dataclasses import replace
import threading
from types import MethodType

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.core.component_naming import default_payload_name
from automation_harness.authoring.gui.repository_window import ObjectRepositoryWindow
from automation_harness.authoring.object_identity_workbench import open_capture_workbench
from automation_harness.authoring.object_reference_updates import (
    apply_project_reference_updates,
    preview_project_reference_updates,
)
from automation_harness.drivers.javafx_bridge import JavaFxBridgeUnavailable
from automation_harness.core.menu_inventory import MENU_OWNER_TYPES, with_inventory_metadata
from automation_harness.core.repository_hierarchy import (
    authoring_parent_ids,
    visible_authoring_definitions,
)
from automation_harness.core.repository_normalization import (
    plan_repository_normalization,
)


class WorkbenchObjectRepositoryWindow(ObjectRepositoryWindow):
    """Hierarchical Object Repository editor backed by Object Identity Workbench.

    Logical objects are framework agnostic. Readable authored names and semantic
    ownership are separate from framework-specific locator mechanisms, which
    live only in the ordered strategy alternatives on each object.
    """

    def __init__(self, *args, **kwargs):
        self._capture_workbench = None
        self._workbench_original_definition = None
        self._workbench_original_component_id = None
        super().__init__(*args, **kwargs)
        self.button("Capture Next Click", self.capture_next_click)
        self.button("Refresh Menu Inventory", self.refresh_menu_inventory)
        self.button("Normalize Repository", self.normalize_repository)
        self._rename_legacy_edit_button(self.root)
        self._install_hierarchical_tree()
        self.refresh()

    def _rename_legacy_edit_button(self, widget):
        if isinstance(widget, Gtk.Button) and widget.get_label() == "Edit Definition":
            widget.set_label("Edit in Workbench")
        if hasattr(widget, "get_children"):
            for child in widget.get_children():
                self._rename_legacy_edit_button(child)

    def _install_hierarchical_tree(self):
        self.store = Gtk.TreeStore(str, str, str, str, str)
        self.tree.set_model(self.store)
        columns = self.tree.get_columns()
        if columns:
            columns[0].set_title("Object")
        if len(columns) > 2:
            columns[2].set_title("Locator")

    def selected(self, tree, column=0):
        if tree is self.tree:
            model, iterator = tree.get_selection().get_selected()
            if iterator is None:
                return None
            component_id = model.get_value(iterator, 4)
            return component_id or None
        return super().selected(tree, column)

    def refresh(self):
        selected = self.selected(self.tree) if hasattr(self, "tree") else None
        query = self.search.get_text().strip().casefold() if hasattr(self, "search") else ""
        self.store.clear()

        definitions = {
            definition.object_id: definition
            for definition in visible_authoring_definitions(self.repository)
        }
        parents = authoring_parent_ids(self.repository)
        if query:
            visible = {
                definition.object_id
                for definition in definitions.values()
                if query in " ".join((
                    definition.component_id,
                    definition.description,
                    definition.object_type.value,
                )).casefold()
            }
            for object_id in tuple(visible):
                parent_id = parents.get(object_id)
                while parent_id is not None and parent_id in definitions:
                    if parent_id in visible:
                        break
                    visible.add(parent_id)
                    parent_id = parents.get(parent_id)
        else:
            visible = set(definitions)

        children = {}
        for object_id in visible:
            parent_id = parents.get(object_id)
            if parent_id not in visible:
                parent_id = None
            children.setdefault(parent_id, []).append(object_id)

        def append_branch(parent_iter, object_id):
            definition = definitions[object_id]
            properties = dict(definition.properties or {})
            locator = str(properties.get("locator_status") or "ready").replace("_", " ").title()
            inventory = properties.get("menu_inventory_status")
            if inventory:
                locator += " · Menu %s" % str(inventory).title()
            iterator = self.store.append(parent_iter)
            self.store.set_value(iterator, 0, definition.component_id)
            self.store.set_value(
                iterator, 1,
                definition.object_type.value.replace("_", " ").title(),
            )
            self.store.set_value(iterator, 2, locator)
            self.store.set_value(iterator, 3, str(definition.revision))
            self.store.set_value(iterator, 4, definition.component_id)
            for child_id in sorted(
                children.get(object_id, ()),
                key=lambda value: definitions[value].component_id.casefold(),
            ):
                append_branch(iterator, child_id)

        for object_id in sorted(
            children.get(None, ()),
            key=lambda value: definitions[value].component_id.casefold(),
        ):
            append_branch(None, object_id)

        self.tree.expand_all()
        shown = len(visible)
        self.set_status(
            "%d of %d objects" % (shown, len(definitions))
            if query else "%d objects" % shown
        )
        if selected:
            iterator = self._find_component_iter(selected)
            if iterator is not None:
                self.tree.get_selection().select_iter(iterator)
                self.tree.scroll_to_cell(
                    self.store.get_path(iterator), None, False, 0.0, 0.0,
                )
        self.show_selected()

    def _find_component_iter(self, component_id):
        def visit(iterator):
            while iterator is not None:
                if self.store.get_value(iterator, 4) == component_id:
                    return iterator
                child = self.store.iter_children(iterator)
                if child is not None:
                    found = visit(child)
                    if found is not None:
                        return found
                iterator = self.store.iter_next(iterator)
            return None
        return visit(self.store.get_iter_first())

    def show_selected(self):
        component_id = self.selected(self.tree)
        if not component_id:
            self.detail.get_buffer().set_text("")
            return
        definition = self.repository.get(component_id)
        properties = dict(definition.properties or {})
        parents = authoring_parent_ids(self.repository)
        child_count = sum(
            1 for owner in parents.values()
            if owner == definition.object_id
        )
        lines = [
            "Object: %s" % component_id,
            "Type: %s" % definition.object_type.value.replace("_", " ").title(),
            "Revision: %s" % definition.revision,
            "Actions: %s" % (", ".join(sorted(definition.actions)) or "none"),
            "Locator status: %s" % str(
                properties.get("locator_status") or "ready"
            ).replace("_", " ").title(),
            "Semantic children: %d" % child_count,
        ]
        if definition.object_type in MENU_OWNER_TYPES:
            lines.extend((
                "Menu inventory: %s" % str(
                    properties.get("menu_inventory_status") or "unknown"
                ).title(),
                "Menu options: %s" % properties.get(
                    "menu_inventory_item_count", len(definition.subobjects),
                ),
                "Inventory source: %s" % str(
                    properties.get("menu_inventory_source") or "unknown"
                ).replace("_", " "),
            ))
        lines.extend((
            "",
            "Framework-specific locator details are intentionally hidden here.",
            "Use Edit in Workbench to inspect or change resolver identity.",
        ))
        self.detail.get_buffer().set_text("\n".join(lines))

    def capture_next_click(self):
        if not getattr(self.capture, "available", True):
            return self.error("Object Capture", "No supported live desktop capture backend is available.")
        self.set_status("Waiting for next physical click…")
        self.window.hide()

        def worker():
            try:
                captured = self.capture.capture_next_click(timeout=30.0)
            except Exception as exc:
                GLib.idle_add(self._capture_next_click_finished, None, exc)
            else:
                GLib.idle_add(self._capture_next_click_finished, captured, None)

        threading.Thread(target=worker, name="repository-next-click-capture", daemon=True).start()

    def _capture_next_click_finished(self, captured, error):
        self.window.show_all()
        self.window.present()
        if error is not None:
            self.set_status("Capture Next Click failed")
            self.error("Capture Next Click", "%s: %s" % (type(error).__name__, error))
            return False
        self._workbench_original_component_id = None
        self._workbench_original_definition = None
        self._configure_workbench(open_capture_workbench(self, captured))
        self.set_status("Click captured — review semantic tree and identity in Object Identity Workbench")
        return False

    def edit_selected(self):
        component_id = self.selected(self.tree)
        if not component_id:
            return self.info("Object Repository", "Select an object first.")
        definition = self.repository.get(component_id)
        self.set_status("Resolving %s for Object Identity Workbench…" % component_id)
        self.window.hide()

        def worker():
            try:
                captured = self._capture_definition(definition)
            except Exception as exc:
                GLib.idle_add(self._edit_resolution_failed, component_id, exc)
            else:
                GLib.idle_add(self._open_existing_workbench, component_id, definition, captured)

        threading.Thread(target=worker, name="repository-workbench-resolver", daemon=True).start()

    def _capture_definition(self, definition):
        errors = []
        for strategy in definition.strategies:
            identity = strategy.options.get("identification")
            try:
                if strategy.type == "javafx":
                    process_id = definition.properties.get("bridge_pid")
                    try:
                        return self.capture.javafx_driver.inspect(
                            identification=identity, process_id=process_id,
                        )
                    except JavaFxBridgeUnavailable:
                        return self.capture.javafx_driver.inspect(identification=identity)
                if strategy.type in {"atspi", "java_accessibility"}:
                    return self.capture.capture_by_locator(identification=identity)
                if strategy.type == "java_agent":
                    return self.capture.java_agent_driver.inspect(
                        identification=identity,
                    )
            except Exception as exc:
                errors.append("%s: %s: %s" % (strategy.type, type(exc).__name__, exc))
        raise LookupError(
            "No live semantic object matched %r. %s" %
            (definition.component_id, "; ".join(errors or ["no editable semantic locator strategy"]))
        )

    def normalize_repository(self):
        try:
            plan = plan_repository_normalization(self.repository)
        except Exception as exc:
            return self.error(
                "Normalize Repository",
                "%s: %s" % (type(exc).__name__, exc),
            )
        if not plan.changed:
            return self.info(
                "Normalize Repository",
                "This repository already uses the semantic authoring model.",
            )

        reference_report = None
        if self.project_context is not None and plan.renames:
            try:
                reference_report = preview_project_reference_updates(
                    self.project_context,
                    self.path,
                    plan.renames,
                )
            except Exception as exc:
                return self.error(
                    "Normalize Repository",
                    "Dependent artifact preview failed: %s: %s"
                    % (type(exc).__name__, exc),
                )

        lines = [
            "Normalization preserves immutable object IDs and locator strategies.",
            "",
            "Readable renames: %d" % len(plan.renames),
            "Structural-only objects hidden: %d" % len(plan.structural_only),
            "Semantic ownership changes: %d" % len(plan.reparented),
        ]
        if reference_report is not None:
            lines.extend((
                "Dependent artifact files affected: %d"
                % len(reference_report.updates),
                "Dependent references rewritten: %d"
                % reference_report.replacement_count,
            ))

        if plan.renames:
            lines.extend(("", "Rename preview:"))
            for old, new in list(plan.renames.items())[:8]:
                lines.append("  %s  →  %s" % (old, new))
            if len(plan.renames) > 8:
                lines.append("  … %d more" % (len(plan.renames) - 8))

        if plan.structural_only:
            lines.extend(("", "Hidden structural objects:"))
            for name in plan.structural_only[:6]:
                lines.append("  %s" % name)
            if len(plan.structural_only) > 6:
                lines.append("  … %d more" % (len(plan.structural_only) - 6))

        if plan.reparented:
            lines.extend(("", "Ownership preview:"))
            for name, old_parent, new_parent in plan.reparented[:8]:
                lines.append(
                    "  %s: %s  →  %s"
                    % (name, old_parent or "(root)", new_parent or "(root)")
                )
            if len(plan.reparented) > 8:
                lines.append("  … %d more" % (len(plan.reparented) - 8))

        if self.project_context is None and plan.renames:
            lines.extend((
                "",
                "Warning: this repository was opened without Project context.",
                "Alias changes cannot be propagated to external Test Plans or Step Registries automatically.",
                "The normalized repository will remain unsaved until you choose Save.",
            ))
        elif self.project_context is not None:
            lines.extend((
                "",
                "Applying will atomically save the repository and update project artifacts bound to it.",
            ))

        if not self.confirm("Normalize Repository", "\n".join(lines)):
            self.set_status("Repository normalization cancelled")
            return

        try:
            if self.project_context is not None:
                report = apply_project_reference_updates(
                    self.project_context,
                    self.path,
                    plan.renames,
                    repository=plan.repository,
                )
                self.repository = plan.repository
                self.mark_dirty(False)
                self.refresh()
                self.set_status(
                    "Repository normalized — %d artifact file(s) updated, %d reference(s) rewritten"
                    % (len(report.updates), report.replacement_count)
                )
            else:
                self.repository = plan.repository
                self.mark_dirty(True)
                self.refresh()
                self.set_status(
                    "Repository normalized in memory — Save Repository to persist"
                )
        except Exception as exc:
            self.error(
                "Normalize Repository",
                "%s: %s" % (type(exc).__name__, exc),
            )
            self.set_status("Repository normalization failed")

    def refresh_menu_inventory(self):
        component_id = self.selected(self.tree)
        if not component_id:
            return self.info("Refresh Menu Inventory", "Select a menu object first.")
        definition = self.repository.get(component_id)
        if definition.object_type not in MENU_OWNER_TYPES:
            return self.info(
                "Refresh Menu Inventory",
                "Select a Menu, Menu Bar, or Context Menu object.",
            )
        self.set_status("Refreshing menu inventory for %s…" % component_id)
        self.window.hide()

        def worker():
            try:
                captured = self._capture_definition(definition)
                if not captured.logical_subobjects:
                    raise ValueError(
                        "live menu capture did not expose a menu inventory"
                    )
            except Exception as exc:
                GLib.idle_add(
                    self._menu_inventory_refresh_finished,
                    component_id, None, exc,
                )
            else:
                GLib.idle_add(
                    self._menu_inventory_refresh_finished,
                    component_id, captured, None,
                )

        threading.Thread(
            target=worker,
            name="repository-menu-inventory-refresh",
            daemon=True,
        ).start()

    def _menu_inventory_refresh_finished(self, component_id, captured, error):
        self.window.show_all()
        self.window.present()
        if error is not None:
            self.set_status("Menu inventory refresh failed")
            self.error(
                "Refresh Menu Inventory",
                "%s: %s" % (type(error).__name__, error),
            )
            return False

        current = self.repository.get(component_id)
        strategy_type = captured.candidate_strategy().type
        complete = strategy_type in {"java_agent", "javafx"}
        source = (
            "native_java_model" if strategy_type == "java_agent"
            else "javafx_model" if strategy_type == "javafx"
            else "accessibility_snapshot"
        )
        old_count = dict(current.properties or {}).get(
            "menu_inventory_item_count", len(current.subobjects),
        )
        new_properties = {
            key: value for key, value in dict(current.properties or {}).items()
            if not str(key).startswith("menu_inventory_")
        }
        new_properties = with_inventory_metadata(
            new_properties,
            current.object_type,
            captured.logical_subobjects,
            complete=complete,
            source=source,
        )
        updated = replace(
            current,
            subobjects={
                str(key): dict(value)
                for key, value in captured.logical_subobjects.items()
            },
            properties=new_properties,
            revision=current.revision + 1,
        )
        new_count = new_properties.get(
            "menu_inventory_item_count", len(updated.subobjects),
        )
        if not self.confirm(
            "Replace Menu Inventory",
            "Replace the stored menu inventory for %s?\n\n"
            "Options: %s → %s\n"
            "Inventory status: %s"
            % (
                component_id,
                old_count,
                new_count,
                new_properties.get("menu_inventory_status", "unknown"),
            ),
        ):
            self.set_status("Menu inventory refresh discarded")
            return False

        self.repository = self.repository.with_component(updated)
        self.mark_dirty()
        self.refresh()
        self.set_status(
            "Refreshed menu inventory for %s — Save Repository to persist"
            % component_id
        )
        return False

    def _edit_resolution_failed(self, component_id, error):
        self.window.show_all(); self.window.present()
        self.set_status("Workbench resolution failed")
        self.error(
            "Edit Object",
            "The stored object must resolve against the live application before its capture identity can be edited.\n\n%s: %s" %
            (type(error).__name__, error),
        )
        return False

    def _configure_workbench(self, workbench):
        def semantic_default(instance, node):
            base = default_payload_name(node.payload)
            existing = set(self.repository.components)
            existing.update(value for key, value in instance.names.items() if key != node.key)
            if base not in existing:
                return base
            index = 2
            while "%s %d" % (base, index) in existing:
                index += 1
            return "%s %d" % (base, index)

        workbench._default_component_id = MethodType(semantic_default, workbench)
        return workbench

    def _open_existing_workbench(self, component_id, definition, captured):
        self.window.show_all(); self.window.present()
        self._workbench_original_component_id = component_id
        self._workbench_original_definition = definition
        workbench = self._configure_workbench(open_capture_workbench(self, captured))
        self._seed_existing_workbench(workbench, component_id, definition, 0)
        self.set_status("Editing %s in Object Identity Workbench" % component_id)
        return False

    def _seed_existing_workbench(self, workbench, component_id, definition, attempt):
        if workbench.context is None:
            if attempt < 100:
                GLib.timeout_add(50, self._seed_existing_workbench, workbench, component_id, definition, attempt + 1)
            return False
        target_key = workbench.context.target_key
        workbench.names[target_key] = component_id
        for strategy in definition.strategies:
            identity = strategy.options.get("identification")
            if strategy.type in {"javafx", "atspi", "java_accessibility"} and isinstance(identity, dict):
                workbench.identity_overrides[target_key] = identity
                break
        workbench._select_key(target_key)
        node = workbench.nodes.get(target_key)
        if node is not None:
            workbench._render_properties(node)
        return False

    def _capture_pointer_now(self):
        try:
            display = self.window.get_display()
            seat = display.get_default_seat(); pointer = seat.get_pointer()
            _screen, x, y = pointer.get_position()
            captured = self.capture.capture_scoped_at_point(int(x), int(y))
        except Exception as exc:
            self.window.show_all(); self.window.present()
            self.error("Object Capture", "%s: %s" % (type(exc).__name__, exc))
            self.set_status("Capture failed")
            return False
        self.window.show_all(); self.window.present()
        self._workbench_original_component_id = None
        self._workbench_original_definition = None
        self._configure_workbench(open_capture_workbench(self, captured))
        self.set_status("Captured object — review semantic tree and qualified identity in Object Identity Workbench")
        return False

    # ObjectIdentityWorkbench app contract ---------------------------------
    def _error(self, title, message):
        return self.error(title, message)

    def _info(self, title, message):
        return self.info(title, message)

    def _set_status(self, value):
        self.set_status(value)

    def _mark_repository_dirty(self, dirty=True):
        self.mark_dirty(bool(dirty))

    def refresh_objects(self):
        component_id = self._workbench_original_component_id
        original = self._workbench_original_definition
        if component_id and original and component_id in self.repository.components:
            updated = self.repository.get(component_id)
            if updated.object_id != original.object_id:
                updated = replace(
                    updated,
                    object_id=original.object_id,
                    description=original.description,
                    visual=original.visual,
                    action_completion=original.action_completion,
                    scope=original.scope,
                )
                self.repository = self.repository.with_component(updated)

        normalized = self.repository
        for name, definition in tuple(normalized.components.items()):
            if definition.framework is not None or definition.native_class is not None:
                normalized = normalized.with_component(
                    replace(definition, framework=None, native_class=None)
                )
        self.repository = normalized
        self.mark_dirty()
        self.refresh()

    def save_repository(self):
        return self.save()

    def bind_captured_application(self, _captured):
        return True

    def _show_highlight(self, bounds, *_args):
        return super()._show_highlight(bounds)

    def _clear_highlight(self):
        return super()._clear_highlight()


def _semantic_segment(value):
    output = []
    capitalize = True
    for char in str(value or "").strip():
        if char.isalnum():
            output.append(char.upper() if capitalize else char)
            capitalize = False
        else:
            capitalize = True
    return "".join(output) or "Object"
