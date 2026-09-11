"""Editable repository lineage nodes for Object Identity Workbench.

Repository hierarchy nodes are authored name scopes, not inert presentation
containers. Renaming one rewrites every descendant component id while preserving
immutable object ids and locator definitions.
"""
from __future__ import annotations

from dataclasses import replace

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from automation_harness.core.component_repository import ComponentRepository

_INSTALLED = False
_BRANCH_PREFIX = "repo-branch:"


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from automation_harness.authoring.gui.repository_identity_workbench_window import RepositoryIdentityWorkbench

    original_append_tree = RepositoryIdentityWorkbench._append_tree
    original_context_ready = RepositoryIdentityWorkbench._context_ready
    original_render_properties = RepositoryIdentityWorkbench._render_properties
    original_selection_summary = RepositoryIdentityWorkbench._selection_summary
    original_save_repository = RepositoryIdentityWorkbench.save_repository
    original_highlight = RepositoryIdentityWorkbench.highlight_selected

    def append_tree(self, parent_iter, node):
        if not _is_branch(node):
            return original_append_tree(self, parent_iter, node)
        iterator = self.tree_store.append(parent_iter, (
            True, node.label, node.key, True,
        ))
        for child in node.children:
            self._append_tree(iterator, child)
        return iterator

    def context_ready(self, context):
        result = original_context_ready(self, context)
        for node in context.root.walk():
            if not _is_branch(node):
                continue
            self.names.setdefault(node.key, node.label)
            self._set_checked(node.key, True)
        return result

    def selection_summary(self, node):
        if _is_branch(node):
            count = len(_descendant_definition_keys(node))
            return "Repository lineage · %d descendant object(s) · edits cascade to descendants" % count
        return original_selection_summary(self, node)

    def render_properties(self, node):
        if not _is_branch(node):
            return original_render_properties(self, node)
        _render_lineage_editor(self, node)

    def save_repository(self):
        self._remember_selected_edits()
        try:
            renamed = _apply_lineage_renames(self)
        except Exception as exc:
            return self.app._error("Save repository", "%s: %s" % (type(exc).__name__, exc))
        result = original_save_repository(self)
        if renamed:
            self._set_status(
                "Saved repository — %d lineage scope(s) updated and propagated to descendants" % renamed
            )
        return result

    def highlight_selected(self):
        node = self._selected_node()
        if node is not None and _is_branch(node):
            return self.app._info(
                "Highlight lineage",
                "A repository lineage scope is not one live object. Select a descendant object to highlight it.",
            )
        return original_highlight(self)

    RepositoryIdentityWorkbench._append_tree = append_tree
    RepositoryIdentityWorkbench._context_ready = context_ready
    RepositoryIdentityWorkbench._selection_summary = selection_summary
    RepositoryIdentityWorkbench._render_properties = render_properties
    RepositoryIdentityWorkbench.save_repository = save_repository
    RepositoryIdentityWorkbench.highlight_selected = highlight_selected


def _is_branch(node) -> bool:
    return str(getattr(node, "key", "")).startswith(_BRANCH_PREFIX)


def _render_lineage_editor(workbench, node) -> None:
    for child in workbench.properties_box.get_children():
        workbench.properties_box.remove(child)
    workbench.identity_fields = []
    workbench.ordinal_field = None
    workbench.name_entry = None

    frame = Gtk.Frame(label="Lineage Properties")
    grid = Gtk.Grid()
    grid.set_border_width(8)
    grid.set_row_spacing(7)
    grid.set_column_spacing(8)
    frame.add(grid)
    workbench.properties_box.pack_start(frame, False, False, 0)

    path = str(node.payload.get("repository_path") or node.label)
    affected = len(_descendant_definition_keys(node))

    label = Gtk.Label(label="name")
    label.set_halign(Gtk.Align.START)
    grid.attach(label, 0, 0, 1, 1)
    grid.attach(Gtk.Label(label="="), 1, 0, 1, 1)
    entry = Gtk.Entry()
    entry.set_hexpand(True)
    entry.set_text(workbench.names.get(node.key, node.label))
    entry.connect("changed", workbench._name_changed)
    grid.attach(entry, 2, 0, 1, 1)
    scope = Gtk.Label(label="propagates to %d descendant object(s)" % affected)
    scope.set_halign(Gtk.Align.START)
    grid.attach(scope, 3, 0, 1, 1)
    workbench.name_entry = entry

    current = Gtk.Label(label="Qualified lineage: %s" % path)
    current.set_halign(Gtk.Align.START)
    grid.attach(current, 0, 1, 4, 1)

    note = Gtk.Label(label=(
        "Renaming this parent/lineage segment rewrites the qualified component ID of every descendant. "
        "Immutable object IDs and locator identities are preserved; only the authored repository path changes."
    ))
    note.set_halign(Gtk.Align.START)
    note.set_line_wrap(True)
    grid.attach(note, 0, 2, 4, 1)

    workbench.properties_box.show_all()


def _descendant_definition_keys(node):
    result = []
    for item in node.walk():
        if str(getattr(item, "key", "")).startswith("repo-object:"):
            result.append(item.key)
    return result


def _apply_lineage_renames(workbench) -> int:
    """Apply all edited lineage segments as one atomic repository rename map."""
    context = workbench.context
    if context is None:
        return 0

    edits = []
    for node in context.root.walk():
        if not _is_branch(node):
            continue
        original_path = str(node.payload.get("repository_path") or "").strip(".")
        if not original_path:
            continue
        original_segment = original_path.rsplit(".", 1)[-1]
        new_segment = str(workbench.names.get(node.key, node.label) or "").strip()
        if not new_segment:
            raise ValueError("lineage name must not be empty")
        if "." in new_segment:
            raise ValueError("lineage name %r must be one path segment, not a qualified component ID" % new_segment)
        if new_segment != original_segment:
            edits.append((original_path, new_segment, node.key))
    if not edits:
        return 0

    edits.sort(key=lambda item: item[0].count("."))
    repository = workbench._repository_host.repository
    rename_map = _lineage_rename_map(repository, edits)
    if not rename_map:
        return 0

    sources = set(rename_map)
    targets = list(rename_map.values())
    if len(set(targets)) != len(targets):
        raise ValueError("lineage edits would collapse multiple repository objects onto the same component ID")
    for target in targets:
        if target in repository.components and target not in sources:
            raise ValueError("lineage edit would overwrite existing object %r" % target)

    components = {}
    for old_id, definition in repository.components.items():
        new_id = rename_map.get(old_id, old_id)
        components[new_id] = replace(definition, component_id=new_id, revision=definition.revision + (1 if new_id != old_id else 0))
    repository = ComponentRepository(components)
    workbench._repository_host.repository = repository
    workbench.app.repository = repository

    # Keep the workbench's immutable object mapping and leaf names synchronized
    # so the normal repository save pass updates locator edits instead of
    # recreating objects under stale pre-rename names.
    for key, definition in tuple(workbench._definition_by_key.items()):
        current_id = next((name for name, item in repository.components.items() if item.object_id == definition.object_id), None)
        if current_id is None:
            continue
        updated = repository.get(current_id)
        workbench._definition_by_key[key] = updated
        workbench.names[key] = current_id

    workbench.app._mark_repository_dirty(True)
    return len(edits)


def _lineage_rename_map(repository, edits):
    """Return old->new component IDs after applying nested lineage edits.

    Edits are expressed against the original repository paths. Parent edits are
    applied before child edits, so editing both ``Pane`` and ``Pane.Menu`` in a
    single save produces the expected composed path.
    """
    edits = sorted(edits, key=lambda item: item[0].count("."))
    result = {}
    for component_id in repository.components:
        original_parts = component_id.split(".")
        current_parts = list(original_parts)
        for original_path, new_segment, _key in edits:
            path_parts = original_path.split(".")
            depth = len(path_parts)
            if len(original_parts) <= depth:
                # Branches represent ancestors; an object must be below it.
                continue
            if original_parts[:depth] != path_parts:
                continue
            current_parts[depth - 1] = new_segment
        new_id = ".".join(current_parts)
        if new_id != component_id:
            result[component_id] = new_id
    return result
