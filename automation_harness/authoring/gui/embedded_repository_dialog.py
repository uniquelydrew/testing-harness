"""Plan-embedded object inventory and explicit migration conflict decisions."""
from __future__ import annotations

import json

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GObject, Gtk

from automation_harness.core.repository_migration import inspect_embedded_repository
from automation_harness.core.test_plan import repository_from_plan


def review_embedded_repository(parent, plan, canonical=None, *, migrate=False):
    """Return (UUID resolutions, import renames), or None on cancellation.

    This is an inspection of the *plan snapshot*, never the effective execution
    repository. Editing the choices does not mutate any artifact.
    """
    embedded = repository_from_plan(plan)
    inspection = inspect_embedded_repository(
        plan, canonical if canonical is not None else type(embedded)({}),
    )
    title = "Review Embedded Repository Migration" if migrate else "Embedded Object Repository"
    dialog = Gtk.Dialog(title=title, transient_for=parent, modal=True)
    dialog.set_default_size(1100, 650)
    dialog.add_buttons("Cancel" if migrate else "Close", Gtk.ResponseType.CANCEL)
    if migrate:
        dialog.add_button("Prepare Migration", Gtk.ResponseType.OK)
    content = dialog.get_content_area()
    content.set_spacing(8)
    content.set_border_width(10)
    note = Gtk.Label(label=(
        "%d embedded object(s), %d item(s) requiring a decision. UUID matches reuse the canonical object. "
        "New objects import unchanged. Resolve each name collision by explicitly matching "
        "a canonical UUID or renaming the imported object. Exact locator matches under distinct names "
        "also require approval to import as distinct. No changes are made in this view."
        % (inspection.embedded_count, inspection.conflicting_count)
    ))
    note.set_line_wrap(True)
    note.set_xalign(0)
    content.pack_start(note, False, False, 0)
    store = Gtk.ListStore(*([GObject.TYPE_STRING] * 6))
    tree = Gtk.TreeView(model=store)
    for index, (heading, width) in enumerate((
        ("Embedded name", 180), ("UUID", 290), ("Type", 110),
        ("Parent UUID", 260), ("Status", 140), ("Resolution", 180),
    )):
        column = Gtk.TreeViewColumn(heading, Gtk.CellRendererText(), text=index)
        column.set_resizable(True)
        column.set_min_width(width)
        tree.append_column(column)
    for row in inspection.objects:
        definition = embedded.get(row.object_id)
        status = {
            "canonical_uuid": "Existing UUID",
            "canonical_changed": "Changed snapshot",
            "type_conflict": "Type conflict",
            "possible_duplicate": "Locator match",
            "name_conflict": "Name collision",
            "new": "New import",
        }[row.status] if canonical is not None else "Embedded"
        destination = row.canonical_name if row.status in {
            "canonical_uuid", "canonical_changed", "type_conflict", "possible_duplicate",
        } else ""
        if row.status == "possible_duplicate" and row.candidates:
            destination = ", ".join(canonical.get(item).component_id for item in row.candidates)
        store.append((
            row.name, row.object_id, definition.object_type.value,
            row.owner_object_id or "", status, destination,
        ))
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.add(tree)
    content.pack_start(scroll, True, True, 0)
    detail = Gtk.TextView()
    detail.set_editable(False)
    detail.set_monospace(True)
    detail.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    detail.set_size_request(-1, 175)
    detail_scroll = Gtk.ScrolledWindow()
    detail_scroll.add(detail)
    content.pack_start(detail_scroll, False, True, 0)
    resolutions, renames, distinct = {}, {}, set()

    def selected_row():
        model, iterator = tree.get_selection().get_selected()
        return iterator if model is store else None

    def update_detail(*_args):
        iterator = selected_row()
        if iterator is None:
            detail.get_buffer().set_text("")
            return
        definition = embedded.get(store.get_value(iterator, 1))
        payload = embedded.to_document()["components"][definition.component_id]
        rendered = {"embedded": {"component_id": definition.component_id, **payload}}
        if canonical is not None:
            current = canonical.get(definition.object_id) if canonical.contains(definition.object_id) else (
                canonical.components.get(definition.component_id))
            if current is not None:
                rendered["canonical"] = {
                    "component_id": current.component_id,
                    **canonical.to_document()["components"][current.component_id],
                }
        detail.get_buffer().set_text(json.dumps(rendered, indent=2, default=str))

    tree.get_selection().connect("changed", update_detail)

    def prompt_match():
        iterator = selected_row()
        if iterator is None:
            return
        original_id = store.get_value(iterator, 1)
        source = embedded.get(original_id)
        options = tuple(
            definition for definition in canonical.components.values()
            if definition.object_type == source.object_type
        )
        if not options:
            return
        choice = Gtk.Dialog(title="Map embedded UUID to canonical UUID", transient_for=dialog, modal=True)
        choice.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Use Canonical Object", Gtk.ResponseType.OK)
        combo = Gtk.ComboBoxText()
        for definition in sorted(options, key=lambda value: value.component_id.casefold()):
            combo.append(definition.object_id, "%s  [%s]" % (definition.component_id, definition.object_id))
        combo.set_active(0)
        choice.get_content_area().pack_start(combo, False, False, 8)
        choice.show_all()
        response = choice.run()
        target_id = combo.get_active_id()
        choice.destroy()
        if response != Gtk.ResponseType.OK or not target_id:
            return
        if original_id == target_id:
            resolutions.pop(original_id, None)
        else:
            resolutions[original_id] = target_id
        renames.pop(original_id, None)
        distinct.discard(original_id)
        target = canonical.get(target_id)
        store.set_value(iterator, 4, "Mapped UUID")
        store.set_value(iterator, 5, target.component_id)

    def prompt_rename():
        iterator = selected_row()
        if iterator is None:
            return
        object_id = store.get_value(iterator, 1)
        text_dialog = Gtk.Dialog(title="Rename Imported Object", transient_for=dialog, modal=True)
        text_dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Rename Import", Gtk.ResponseType.OK)
        entry = Gtk.Entry()
        entry.set_text(renames.get(object_id, store.get_value(iterator, 0)))
        text_dialog.get_content_area().pack_start(entry, False, False, 8)
        text_dialog.show_all()
        response = text_dialog.run()
        new_name = entry.get_text().strip()
        text_dialog.destroy()
        if response != Gtk.ResponseType.OK or not new_name:
            return
        resolutions.pop(object_id, None)
        renames[object_id] = new_name
        store.set_value(iterator, 4, "Renamed import")
        store.set_value(iterator, 5, new_name)

    def mark_distinct():
        iterator = selected_row()
        if iterator is None:
            return
        object_id = store.get_value(iterator, 1)
        if not isinstance(object_id, str):
            return
        if resolutions.get(object_id):
            return
        approval = Gtk.MessageDialog(
            transient_for=dialog, modal=True, message_type=Gtk.MessageType.WARNING,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Import as a distinct object?",
        )
        approval.format_secondary_text(
            "Matching locator evidence may indicate an existing canonical object. "
            "Only approve a separate UUID if these represent different logical controls."
        )
        ok = approval.run() == Gtk.ResponseType.YES
        approval.destroy()
        if ok:
            distinct.add(object_id)
            store.set_value(iterator, 4, "Distinct import")
            store.set_value(iterator, 5, "Keep UUID")

    if migrate:
        commands = Gtk.Box(spacing=8)
        btn = Gtk.Button(label="Map Selected to Canonical...")
        btn.connect("clicked", lambda *_args: prompt_match())
        commands.pack_start(btn, False, False, 0)
        btn = Gtk.Button(label="Rename Selected Import...")
        btn.connect("clicked", lambda *_args: prompt_rename())
        commands.pack_start(btn, False, False, 0)
        btn = Gtk.Button(label="Approve Distinct Import...")
        btn.connect("clicked", lambda *_args: mark_distinct())
        commands.pack_start(btn, False, False, 0)
        content.pack_start(commands, False, False, 0)
    dialog.show_all()
    response = dialog.run()
    dialog.destroy()
    return (resolutions, renames, tuple(sorted(distinct))) if migrate and response == Gtk.ResponseType.OK else None
