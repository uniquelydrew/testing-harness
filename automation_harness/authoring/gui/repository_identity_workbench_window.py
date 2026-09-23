from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.authoring.capture_context import CaptureContext, CaptureContextNode
from automation_harness.authoring.object_identity_workbench import ObjectIdentityWorkbench
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.hybrid_object_capture import HybridObjectCaptureService
from automation_harness.core.object_identity_sync import rename_repository_component
from automation_harness.core.object_resolution import resolve_repository_object
from automation_harness.core.repository_hierarchy import concrete_parent_ids
from automation_harness.authoring.repository_events import publish as publish_repository_change
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy


class _RepositoryWorkbenchHost:
    """Minimal app contract consumed by ObjectIdentityWorkbench.

    Repository editing is backed by persisted definitions rather than pretending
    the repository is a live desktop capture. Synthetic captures adapt stored
    locators to the existing property editor; saves mutate the original
    ComponentDefinition and preserve immutable object identity and metadata.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        self.repository = ComponentRepository.load((self.path,))
        self.capture = HybridObjectCaptureService()
        self.window = Gtk.Window()
        self.window.set_decorated(False)
        self.window.set_skip_taskbar_hint(True)
        self._highlight_windows = []
        self._dirty = False
        self._capture_workbench = None

    def _error(self, title, text):
        dialog = Gtk.MessageDialog(
            transient_for=self._capture_workbench.window if self._capture_workbench else None,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(str(text)); dialog.run(); dialog.destroy()

    def _info(self, title, text):
        dialog = Gtk.MessageDialog(
            transient_for=self._capture_workbench.window if self._capture_workbench else None,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=title,
        )
        dialog.format_secondary_text(str(text)); dialog.run(); dialog.destroy()

    def _set_status(self, _value):
        return None

    def _mark_repository_dirty(self, dirty=True):
        self._dirty = bool(dirty)

    def refresh_objects(self):
        return None

    def bind_captured_application(self, _captured):
        return True

    def _show_highlight(self, bounds, *_args):
        self._clear_highlight()
        x, y, width, height = (int(round(float(value))) for value in bounds)
        thickness = 4
        provider = Gtk.CssProvider(); provider.load_from_data(b"* { background-color: #ff3b30; }")
        for rx, ry, rw, rh in (
            (x, y, width, thickness),
            (x, y + max(0, height - thickness), width, thickness),
            (x, y, thickness, height),
            (x + max(0, width - thickness), y, thickness, height),
        ):
            edge = Gtk.Window(type=Gtk.WindowType.POPUP)
            edge.set_decorated(False); edge.set_keep_above(True); edge.set_accept_focus(False)
            edge.set_opacity(0.88); edge.move(rx, ry); edge.resize(max(1, rw), max(1, rh))
            edge.get_style_context().add_provider(provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            edge.show_all(); self._highlight_windows.append(edge)

    def _clear_highlight(self):
        for edge in tuple(self._highlight_windows):
            try:
                edge.destroy()
            except Exception:
                pass
        self._highlight_windows = []


class RepositoryIdentityWorkbench(ObjectIdentityWorkbench):
    """Object Identity Workbench operating directly on an Object Repository."""

    def __init__(self, host: _RepositoryWorkbenchHost):
        self.repository_mode = True
        self._repository_host = host
        self._definition_by_key: dict[str, ComponentDefinition] = {}
        captures = tuple(_capture_from_definition(item) for item in host.repository.components.values())
        if not captures:
            captures = (_empty_repository_capture(),)
        super().__init__(host, captures[0], recorded_captures=())
        host._capture_workbench = self
        self.window.set_title("Object Identity Workbench — %s" % host.path.name)
        self._configure_repository_toolbar()

    def _load_context_async(self):
        try:
            context, definitions = _repository_context(self._repository_host.repository)
            self._definition_by_key = definitions
        except Exception as exc:
            GLib.idle_add(self._context_failed, exc)
        else:
            GLib.idle_add(self._context_ready, context)

    def _context_ready(self, context):
        result = super()._context_ready(context)
        for key, definition in self._definition_by_key.items():
            self.names[key] = definition.component_id
            identity = _definition_identity(definition)
            if identity is not None:
                self.identity_overrides[key] = identity
            self._set_checked(key, True)
        if context.target_key:
            self._select_key(context.target_key)
            selected = self.nodes.get(context.target_key)
            if selected is not None and selected.is_semantic:
                self.selected_key = selected.key
                self._render_properties(selected)
        self._set_status("Repository scope loaded — %d object(s) prechecked" % len(self._definition_by_key))
        return result

    def _configure_repository_toolbar(self):
        self._button(self.toolbar, "Recapture Selected", self.recapture_selected)
        self._button(self.toolbar, "Delete Selected", self.delete_selected)
        hide = {
            "Check Siblings", "Check Branch", "Clear Checks",
            "Save Selected", "Save Checked",
        }
        for widget in _walk_widgets(self.window):
            if isinstance(widget, Gtk.Label) and widget.get_text() == "Capture Scope":
                widget.set_text("Repository Scope")
            if isinstance(widget, Gtk.Button) and widget.get_label() in hide:
                widget.hide()
        # Repository membership is changed only by the explicit Delete action.
        columns = self.tree.get_columns()
        if columns:
            cells = columns[0].get_cells()
            if cells:
                cells[0].set_property("activatable", False)
        self.window.show_all()
        for widget in _walk_widgets(self.window):
            if isinstance(widget, Gtk.Button) and widget.get_label() in hide:
                widget.hide()

    def delete_selected(self):
        node = self._selected_node()
        definition = self._definition_by_key.get(node.key) if node is not None else None
        if definition is None:
            return self.app._info("Delete object", "Select a repository object first.")
        repository, removed = self._repository_host.repository.delete_subtree(definition.object_id)
        detail = "Delete %s?" % definition.component_id
        if len(removed) > 1:
            detail += "\n\nThis also deletes %d owned descendant(s):\n%s" % (
                len(removed) - 1, "\n".join(removed[1:]),
            )
        dialog = Gtk.MessageDialog(
            transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO, text="Delete repository object",
        )
        dialog.format_secondary_text(detail)
        response = dialog.run(); dialog.destroy()
        if response != Gtk.ResponseType.YES:
            return
        removed_object_ids = tuple(
            self._repository_host.repository.get(component_id).object_id
            for component_id in removed
        )
        self._repository_host.repository = repository
        self.app.repository = repository
        self._repository_host.repository.save(self._repository_host.path)
        publish_repository_change(
            self._repository_host.path,
            deleted_object_ids=removed_object_ids,
        )
        self._definition_by_key = {}
        self.selected_key = None
        self._set_status("Deleted %d object(s)" % len(removed))
        self._load_context_async()

    def recapture_selected(self):
        node = self._selected_node()
        definition = self._definition_by_key.get(node.key) if node is not None else None
        if node is None or definition is None:
            return self.app._info("Recapture object", "Select a repository object first.")
        dialog = Gtk.Dialog(title="Recapture %s" % definition.component_id, transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Capture", Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(10)
        box.pack_start(Gtk.Label(label="Capture on click number:"), False, False, 0)
        count = Gtk.SpinButton.new_with_range(1, 9, 1)
        count.set_value(1)
        box.pack_start(count, False, False, 0)
        note = Gtk.Label(label="1 captures the next click; higher values discard earlier clicks.")
        note.set_halign(Gtk.Align.START)
        note.set_line_wrap(True)
        box.pack_start(note, False, False, 0)
        dialog.show_all()
        response = dialog.run()
        click_count = count.get_value_as_int()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            return

        self._set_status("Recapture %s: click %d of %d…" % (definition.component_id, click_count, click_count))
        self.window.hide()

        def worker():
            try:
                captured = self.app.capture.capture_next_click(click_count=click_count, timeout=30.0)
                proposed, comparison = self.app.capture.recapture_definition(definition, captured)
            except Exception as exc:
                GLib.idle_add(self._recapture_finished, node.key, None, None, None, exc)
            else:
                GLib.idle_add(self._recapture_finished, node.key, proposed, comparison, captured, None)

        import threading
        threading.Thread(target=worker, name="repository-object-recapture", daemon=True).start()

    def _recapture_finished(self, key, proposed, comparison, captured, error):
        if error is not None:
            self.window.show_all()
            self.window.present()
            self._set_status("Recapture failed")
            self.app._error("Recapture object", "%s: %s" % (type(error).__name__, error))
            return False
        bounds = getattr(captured, "bounds", None)
        if bounds:
            self.window.hide()
            self._repository_host._show_highlight(bounds)
            GLib.timeout_add(1200, self._finish_recapture_review, key, proposed, comparison, captured)
        else:
            self._finish_recapture_review(key, proposed, comparison, captured)
        return False

    def _finish_recapture_review(self, key, proposed, comparison, captured=None):
        self._repository_host._clear_highlight()
        self.window.show_all()
        self.window.present()
        changes = comparison.get("changed", {})
        lines = [
            "Object: %s" % comparison["component_id"],
            "Immutable object ID: %s" % comparison["object_id"],
            "Revision: %s → %s" % (comparison["previous_revision"], comparison["proposed_revision"]),
            "",
            "Stable identity changes: %d" % len(comparison.get("stable_changes", {})),
            "Mutable/runtime changes: %d" % len(comparison.get("mutable_changes", {})),
            "Other changes requiring review: %d" % len(comparison.get("review_changes", {})),
        ]
        if changes:
            lines.append("")
            for path, item in list(changes.items())[:12]:
                lines.append("%s [%s]: %s → %s" % (
                    path, item["classification"], item.get("before"), item.get("after"),
                ))
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.CANCEL,
            text="Review recaptured object",
        )
        dialog.add_button("Use Capture", Gtk.ResponseType.OK)
        dialog.format_secondary_text("\n".join(lines))
        response = dialog.run()
        dialog.destroy()
        if response != Gtk.ResponseType.OK:
            self._set_status("Recapture discarded; repository unchanged")
            return False

        definition = self._definition_by_key.get(key)
        if definition is None:
            return False
        updated = self._repository_host.repository.with_component(proposed)
        self._repository_host.repository = updated
        self.app.repository = updated
        self._definition_by_key[key] = proposed
        identity = _definition_identity(proposed)
        if identity is not None:
            self.identity_overrides[key] = identity
        if self.context is not None and isinstance(self.context.captured_by_key, dict):
            # Replace the synthetic old capture so the next highlight/edit is
            # based on the fresh live object.
            self.context.captured_by_key[key] = proposed_capture = _capture_from_definition(proposed)
        self.app._mark_repository_dirty(True)
        self._render_properties(self.nodes[key])
        self._set_status("Recaptured %s at revision %s — Save Repository to persist" % (
            proposed.component_id, proposed.revision,
        ))
        return False

    def _selection_summary(self, node):
        if node.key in self._definition_by_key:
            definition = self._definition_by_key[node.key]
            return "Repository object · %s · immutable object ID %s" % (
                definition.object_type.value,
                definition.object_id + (
                    " · NEEDS RECAPTURE"
                    if dict(definition.properties or {}).get("locator_status") == "needs_recapture"
                    else ""
                ),
            )
        return "Repository hierarchy"

    def highlight_selected(self):
        """Resolve the selected persisted object through the shared dispatcher."""
        node = self._selected_node()
        definition = self._definition_by_key.get(node.key) if node is not None else None
        if definition is None:
            return self.app._info("Highlight object", "Select a repository object first.")
        self._highlight_generation += 1
        generation = self._highlight_generation
        self._set_status("Resolving %s for highlight…" % definition.component_id)

        def worker():
            try:
                result = resolve_repository_object(
                    self.app.capture, self._repository_host.repository, definition,
                )
            except Exception as exc:
                GLib.idle_add(self._highlight_resolution_failed, generation, exc)
            else:
                GLib.idle_add(self._repository_highlight_ready, generation, result)

        import threading
        threading.Thread(target=worker, name="repository-highlight-resolver", daemon=True).start()

    def _repository_highlight_ready(self, generation, result):
        if generation != self._highlight_generation:
            return False
        self.app._show_highlight(result.bounds, False)
        GLib.timeout_add(1400, self._clear_highlight)
        self._set_status("Highlighted %s via %s" % (result.definition.component_id, result.strategy))
        return False

    def _render_properties(self, node):
        """Render persisted locator identity directly for every resolver type."""
        for child in self.properties_box.get_children():
            self.properties_box.remove(child)
        self.identity_fields = []
        self.ordinal_field = None
        self.name_entry = None

        definition = self._definition_by_key.get(node.key)
        if definition is None:
            self._add_message("Select a repository object to edit its identity.")
            self.properties_box.show_all()
            return

        captured = self._captured_for_node(node)
        identity = self.identity_overrides.get(node.key)
        if identity is None:
            identity = _definition_identity(definition) or {"mandatory": {}}
        inherited = self.context.inherited_descriptors(node.key) if self.context else {}
        common = self.context.common_peer_descriptors(node.key) if self.context else {}
        framework = str(getattr(captured, "framework", "") or "")
        self._build_property_inventory(node, identity, inherited, common, framework)
        self.properties_box.show_all()

    def _save_node(self, node, component_id, identity):
        definition = self._definition_by_key.get(node.key)
        if definition is None:
            raise ValueError("repository hierarchy nodes cannot be saved as objects")

        repository = self._repository_host.repository
        current_id = _component_id_for_object_id(repository, definition.object_id)
        if current_id is None:
            raise KeyError("repository object %s is no longer present" % definition.object_id)
        if current_id != component_id:
            repository = rename_repository_component(repository, current_id, component_id)

        current = repository.get(component_id)
        strategies = list(current.strategies)
        index = _editable_strategy_index(current)
        if index is None:
            strategies.insert(0, ComponentStrategy("atspi", {"identification": dict(identity)}))
        else:
            strategies[index] = _strategy_with_identity(strategies[index], identity)
        updated = replace(
            current,
            component_id=component_id,
            strategies=tuple(strategies),
            revision=current.revision + 1,
        )
        self._repository_host.repository = repository.with_component(updated)
        self.app.repository = self._repository_host.repository
        self._definition_by_key[node.key] = updated
        self.app._mark_repository_dirty(True)

    def save_repository(self):
        # In repository mode Save Selected, Save Checked, and Save Repository are
        # intentionally collapsed into this one operation. Every object already
        # belongs to the loaded repository; only changed definitions are updated.
        self._remember_selected_edits()
        errors = []
        saved = 0
        for key, definition in tuple(self._definition_by_key.items()):
            node = self.nodes.get(key)
            if node is None:
                continue
            try:
                component_id = self.names.get(key, definition.component_id).strip()
                if not component_id:
                    raise ValueError("name is required")
                identity = self.identity_overrides.get(key) or _definition_identity(definition)
                if identity is None:
                    raise ValueError("object has no editable locator identity")
                current_id = _component_id_for_object_id(self._repository_host.repository, definition.object_id)
                current = self._repository_host.repository.get(current_id) if current_id else None
                changed = (
                    current is None
                    or component_id != current.component_id
                    or identity != _definition_identity(current)
                )
                if changed:
                    self._save_node(node, component_id, identity)
                    saved += 1
            except Exception as exc:
                errors.append("%s: %s" % (definition.component_id, exc))
        if errors:
            return self.app._error("Save repository", "Repository was not saved.\n\n%s" % "\n".join(errors[:12]))
        self._repository_host.repository.save(self._repository_host.path)
        publish_repository_change(
            self._repository_host.path,
            changed_object_ids=tuple(item.object_id for item in self._repository_host.repository.components.values()),
        )
        self.app.repository = self._repository_host.repository
        self.app._mark_repository_dirty(False)
        self._set_status("Saved repository%s" % (" — %d object(s) updated" % saved if saved else ""))

    def _on_destroy(self, *_args):
        super()._on_destroy(*_args)
        try:
            self._repository_host._clear_highlight()
            self._repository_host.window.destroy()
        except Exception:
            pass


class RepositoryIdentityWorkbenchWindow:
    """Artifact-router facade around the repository-backed workbench."""

    def __init__(self, path, *, project_context=None, opener=None):
        self.path = Path(path).resolve()
        self.project_context = project_context
        self.opener = opener
        self.launching_window = None
        self.host = _RepositoryWorkbenchHost(self.path)
        self.workbench = RepositoryIdentityWorkbench(self.host)
        self.window = self.workbench.window

    def finish_build(self):
        return self

    def set_status(self, value):
        self.workbench._set_status(value)


def _repository_context(repository: ComponentRepository):
    root = CaptureContextNode(
        key="repository-root", label="Object Repository", payload={},
        is_window_root=True, is_semantic=False,
    )
    captured_by_key = {}
    definitions = {}
    target_keys = []
    nodes_by_id = {}

    for definition in sorted(repository.components.values(), key=lambda item: item.component_id):
        key = "repo-object:" + definition.object_id
        capture = _capture_from_definition(definition)
        payload = _capture_payload(capture, definition)
        node = CaptureContextNode(
            key=key, label=definition.component_id.rsplit(".", 1)[-1], payload=payload,
            is_target=True, is_semantic=True,
        )
        nodes_by_id[definition.object_id] = node
        captured_by_key[key] = capture
        definitions[key] = definition
        target_keys.append(key)

    # Only real ComponentDefinitions may occupy the hierarchy.  Legacy dotted
    # names are not converted into pseudo-objects; when an actual prefix object
    # exists it can be inferred as the concrete owner for display/migration.
    parent_ids = concrete_parent_ids(repository)
    for definition in sorted(repository.components.values(), key=lambda item: item.component_id):
        owner_id = parent_ids[definition.object_id]
        parent = nodes_by_id.get(owner_id, root)
        parent.children.append(nodes_by_id[definition.object_id])

    target_key = target_keys[0] if target_keys else "repository-root"
    context = CaptureContext(
        framework="repository", root=root, target_key=target_key,
        target_keys=tuple(target_keys), captured_by_key=captured_by_key,
    )
    return context, definitions


def _capture_from_definition(definition: ComponentDefinition) -> CapturedComponent:
    strategy = _editable_strategy(definition)
    identity = strategy.options.get("identification", {}) if isinstance(strategy.options, Mapping) else {}
    mandatory = identity.get("mandatory", identity) if isinstance(identity, Mapping) else {}
    assistive = identity.get("assistive", {}) if isinstance(identity, Mapping) else {}
    mandatory = mandatory if isinstance(mandatory, Mapping) else {}
    assistive = assistive if isinstance(assistive, Mapping) else {}

    def value(*keys):
        for key in keys:
            if key in mandatory and mandatory[key] not in (None, ""):
                return mandatory[key]
            if key in assistive and assistive[key] not in (None, ""):
                return assistive[key]
        return None

    framework = definition.framework or strategy.type
    if framework == "java_accessibility":
        framework = "java"
    return CapturedComponent(
        name=str(value("name", "text", "accessible_text") or definition.component_id),
        role=str(value("role", "accessible_role") or definition.object_type.value),
        description=definition.description,
        accessible_id=str(value("accessible_id", "id")) if value("accessible_id", "id") is not None else None,
        application=str(value("application")) if value("application") is not None else None,
        window=str(value("window")) if value("window") is not None else None,
        hierarchy=tuple(definition.component_id.split(".")),
        actions=tuple(definition.actions),
        bounds=None,
        state=ComponentState(present=True),
        backend_properties={
            **dict(definition.properties),
            "repository_component_id": definition.component_id,
            "repository_object_id": definition.object_id,
        },
        authored_strategy=strategy,
        object_type=definition.object_type,
        framework=framework,
        native_class=definition.native_class,
        logical_subobjects=definition.subobjects,
    )


def _capture_payload(capture: CapturedComponent, definition: ComponentDefinition):
    strategy = _editable_strategy(definition)
    identity = strategy.options.get("identification", {}) if isinstance(strategy.options, Mapping) else {}
    return {
        "repository_component_id": definition.component_id,
        "repository_object_id": definition.object_id,
        "id": capture.accessible_id,
        "accessible_role": capture.role,
        "accessible_text": capture.name,
        "class": definition.native_class,
        "properties": dict(definition.properties),
        "identification": identity,
    }


def _editable_strategy(definition: ComponentDefinition) -> ComponentStrategy:
    index = _editable_strategy_index(definition)
    if index is not None:
        return definition.strategies[index]
    if definition.strategies:
        return definition.strategies[0]
    return ComponentStrategy("atspi", {"identification": {"mandatory": {"name": definition.component_id}}})


def _editable_strategy_index(definition: ComponentDefinition):
    for index, strategy in enumerate(definition.strategies):
        if strategy.type in {"javafx", "atspi", "java_accessibility"}:
            return index
    return 0 if definition.strategies else None


def _definition_identity(definition: ComponentDefinition):
    strategy = _editable_strategy(definition)
    value = strategy.options.get("identification") if isinstance(strategy.options, Mapping) else None
    return dict(value) if isinstance(value, Mapping) else None


def _strategy_with_identity(strategy: ComponentStrategy, identity: Mapping[str, Any]):
    options = dict(strategy.options)
    options["identification"] = dict(identity)
    return ComponentStrategy(strategy.type, options)


def _component_id_for_object_id(repository: ComponentRepository, object_id: str):
    for component_id, definition in repository.components.items():
        if definition.object_id == object_id:
            return component_id
    return None


def _empty_repository_capture():
    return CapturedComponent(
        name="Object Repository", role="repository", description=None,
        accessible_id=None, application=None, window=None, hierarchy=(),
        actions=(), bounds=None, state=ComponentState(present=True),
    )


def _walk_widgets(widget):
    yield widget
    if hasattr(widget, "get_children"):
        for child in widget.get_children():
            for item in _walk_widgets(child):
                yield item
