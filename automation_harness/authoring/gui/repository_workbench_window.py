from __future__ import annotations

from dataclasses import replace
import threading
from types import MethodType

from gi.repository import GLib, Gtk

from automation_harness.authoring.capture_context import suggested_name
from automation_harness.authoring.gui.repository_window import ObjectRepositoryWindow
from automation_harness.authoring.object_identity_workbench import open_capture_workbench
from automation_harness.drivers.javafx_bridge import JavaFxBridgeUnavailable


class WorkbenchObjectRepositoryWindow(ObjectRepositoryWindow):
    """Hierarchical Object Repository editor backed by Object Identity Workbench.

    Logical objects are framework agnostic. Their qualified semantic path is the
    repository name; framework-specific locator mechanisms live only in the
    ordered strategy alternatives on each object.
    """

    def __init__(self, *args, **kwargs):
        self._capture_workbench = None
        self._workbench_original_definition = None
        self._workbench_original_component_id = None
        super().__init__(*args, **kwargs)
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
            columns[2].set_title("Resolvers")

    def _append_tree_row(self, parent, values):
        """Append a TreeStore row without relying on newer PyGObject overloads.

        RHEL 8/Python 3.6's Gtk override exposes TreeStore.append(parent) rather
        than the newer append(parent, row) convenience signature. Populate the
        returned iterator explicitly so the routed repository editor works on
        the target platform as well as newer development environments.
        """
        iterator = self.store.append(parent)
        for column, value in enumerate(values):
            self.store.set_value(iterator, column, value)
        return iterator

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
        visible = 0
        branches = {}
        for component_id, definition in sorted(self.repository.components.items()):
            resolver_names = sorted({strategy.type for strategy in definition.strategies})
            searchable = " ".join((
                component_id,
                definition.description,
                definition.object_type.value,
                " ".join(resolver_names),
            )).casefold()
            if query and query not in searchable:
                continue
            segments = [segment for segment in component_id.split(".") if segment] or [component_id]
            parent = None
            prefix = []
            for segment in segments[:-1]:
                prefix.append(segment)
                key = tuple(prefix)
                iterator = branches.get(key)
                if iterator is None:
                    iterator = self._append_tree_row(parent, (segment, "", "", "", ""))
                    branches[key] = iterator
                parent = iterator
            self._append_tree_row(parent, (
                segments[-1],
                definition.object_type.value,
                ", ".join(resolver_names),
                str(definition.revision),
                component_id,
            ))
            visible += 1
        self.tree.expand_all()
        self.set_status(
            "%d of %d objects" % (visible, len(self.repository.components))
            if query else "%d objects" % visible
        )
        if selected:
            iterator = self._find_component_iter(selected)
            if iterator is not None:
                self.tree.get_selection().select_iter(iterator)
                self.tree.scroll_to_cell(self.store.get_path(iterator), None, False, 0.0, 0.0)
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
        strategies = []
        for strategy in definition.strategies:
            identity = strategy.options.get("identification")
            strategies.append({
                "type": strategy.type,
                "mandatory": list((identity or {}).get("mandatory", {}).keys()) if isinstance(identity, dict) else [],
                "assistive": list((identity or {}).get("assistive", {}).keys()) if isinstance(identity, dict) else [],
                "ordinal": (identity or {}).get("ordinal") if isinstance(identity, dict) else None,
            })
        lines = [
            "Object: %s" % component_id,
            "Type: %s" % definition.object_type.value,
            "Revision: %s" % definition.revision,
            "Actions: %s" % (", ".join(sorted(definition.actions)) or "none"),
            "Resolution alternatives: %d" % len(definition.strategies),
            "",
            "Identity strategies:",
        ]
        for index, strategy in enumerate(strategies, 1):
            lines.append("  %d. %s" % (index, strategy["type"]))
            lines.append("     mandatory: %s" % (", ".join(strategy["mandatory"]) or "none"))
            lines.append("     assistive: %s" % (", ".join(strategy["assistive"]) or "none"))
            if strategy["ordinal"] is not None:
                lines.append("     ordinal: %s" % strategy["ordinal"])
        lines.extend((
            "",
            "The logical object is framework agnostic; resolver technology belongs to each strategy.",
            "Use Edit in Workbench to inspect the semantic tree and change identity properties.",
        ))
        self.detail.get_buffer().set_text("\n".join(lines))

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
            except Exception as exc:
                errors.append("%s: %s: %s" % (strategy.type, type(exc).__name__, exc))
        raise LookupError(
            "No live semantic object matched %r. %s" %
            (definition.component_id, "; ".join(errors or ["no editable semantic locator strategy"]))
        )

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
        def qualified_default(instance, node):
            if instance.context is None:
                return suggested_name(node.payload)
            path = instance.context.path_to(node.key)
            segments = []
            for item in path:
                if item.key == node.key:
                    value = suggested_name(item.payload)
                elif item.is_window_root:
                    value = _semantic_segment(item.label)
                else:
                    value = instance.names.get(item.key) or suggested_name(item.payload)
                value = _semantic_segment(value)
                if value and (not segments or value != segments[-1]):
                    segments.append(value)
            return ".".join(segments) or "Object"

        workbench._default_component_id = MethodType(qualified_default, workbench)
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

        # Framework/class are properties of an observation/resolver, not of the
        # logical repository object. Normalize every object touched by the
        # workbench so newly captured objects follow the same rule as edits.
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
