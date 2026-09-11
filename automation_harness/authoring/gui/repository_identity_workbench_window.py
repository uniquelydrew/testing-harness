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
        hide = {
            "Check Siblings", "Check Branch", "Clear Checks",
            "Save Selected", "Save Checked",
        }
        for widget in _walk_widgets(self.window):
            if isinstance(widget, Gtk.Label) and widget.get_text() == "Capture Scope":
                widget.set_text("Repository Scope")
            if isinstance(widget, Gtk.Button) and widget.get_label() in hide:
                widget.hide()
        # Repository membership is fixed while editing. Checked rows communicate
        # that every persisted object participates, but are not user toggles.
        columns = self.tree.get_columns()
        if columns:
            cells = columns[0].get_cells()
            if cells:
                cells[0].set_property("activatable", False)
        self.window.show_all()
        for widget in _walk_widgets(self.window):
            if isinstance(widget, Gtk.Button) and widget.get_label() in hide:
                widget.hide()

    def _selection_summary(self, node):
        if node.key in self._definition_by_key:
            definition = self._definition_by_key[node.key]
            return "Repository object · %s · immutable object ID %s" % (
                definition.object_type.value, definition.object_id,
            )
        return "Repository hierarchy"

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
    branches: dict[tuple[str, ...], CaptureContextNode] = {(): root}
    captured_by_key = {}
    definitions = {}
    target_keys = []

    for definition in sorted(repository.components.values(), key=lambda item: item.component_id):
        parts = tuple(part for part in definition.component_id.split(".") if part) or (definition.component_id,)
        parent = root
        prefix: tuple[str, ...] = ()
        for part in parts[:-1]:
            prefix = prefix + (part,)
            branch = branches.get(prefix)
            if branch is None:
                branch = CaptureContextNode(
                    key="repo-branch:" + ".".join(prefix), label=part,
                    payload={"repository_path": ".".join(prefix)},
                    is_semantic=False,
                )
                parent.children.append(branch)
                branches[prefix] = branch
            parent = branch

        key = "repo-object:" + definition.object_id
        capture = _capture_from_definition(definition)
        payload = _capture_payload(capture, definition)
        node = CaptureContextNode(
            key=key, label=parts[-1], payload=payload,
            is_target=True, is_semantic=True,
        )
        parent.children.append(node)
        captured_by_key[key] = capture
        definitions[key] = definition
        target_keys.append(key)

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
