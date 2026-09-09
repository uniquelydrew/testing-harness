from __future__ import annotations

from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


class ArtifactWindow:
    """Common document-window behavior without conflating artifact workflows."""

    title_prefix = "Automation Harness"

    def __init__(self, path=None, *, project_context=None, opener=None):
        self.path = Path(path).resolve() if path is not None else None
        self.project_context = Path(project_context).resolve() if project_context is not None else None
        self.opener = opener
        self.dirty = False
        self.window = Gtk.Window()
        self.window.set_default_size(1180, 760)
        self.window.connect("delete-event", self._delete_event)
        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.root.set_border_width(6)
        self.window.add(self.root)
        self.toolbar = Gtk.Box(spacing=6)
        self.root.pack_start(self.toolbar, False, False, 0)
        self.status = Gtk.Label(label="Ready")
        self.status.set_halign(Gtk.Align.END)
        self.toolbar.pack_end(self.status, True, True, 0)

    def finish_build(self):
        self._update_title()
        self.window.show_all()
        return self

    def _update_title(self):
        name = self.path.name if self.path is not None else "Untitled"
        suffix = " *" if self.dirty else ""
        self.window.set_title("%s — %s%s" % (self.title_prefix, name, suffix))

    def mark_dirty(self, value=True):
        self.dirty = bool(value)
        self._update_title()

    def set_status(self, text):
        self.status.set_text(str(text))

    def button(self, label, callback, *, parent=None):
        button = Gtk.Button(label=label)
        button.connect("clicked", lambda *_args: callback())
        (parent or self.toolbar).pack_start(button, False, False, 0)
        return button

    @staticmethod
    def scrolled(widget):
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(widget)
        return scroll

    @staticmethod
    def list_tree(columns):
        store = Gtk.ListStore(*([str] * len(columns)))
        tree = Gtk.TreeView(model=store)
        for index, (title, width) in enumerate(columns):
            renderer = Gtk.CellRendererText()
            column = Gtk.TreeViewColumn(title, renderer, text=index)
            column.set_resizable(True)
            column.set_min_width(width)
            tree.append_column(column)
        return tree, store

    @staticmethod
    def selected(tree, column=0):
        model, iterator = tree.get_selection().get_selected()
        return model.get_value(iterator, column) if iterator is not None else None

    def info(self, title, text):
        return self._message(Gtk.MessageType.INFO, title, text)

    def error(self, title, text):
        return self._message(Gtk.MessageType.ERROR, title, text)

    def confirm(self, title, text):
        return self._message(Gtk.MessageType.QUESTION, title, text, Gtk.ButtonsType.YES_NO) == Gtk.ResponseType.YES

    def _message(self, kind, title, text, buttons=Gtk.ButtonsType.OK):
        dialog = Gtk.MessageDialog(
            transient_for=self.window,
            modal=True,
            message_type=kind,
            buttons=buttons,
            text=title,
        )
        dialog.format_secondary_text(str(text))
        response = dialog.run()
        dialog.destroy()
        return response

    def ask_text(self, title, prompt, initial=""):
        dialog = Gtk.Dialog(title=title, transient_for=self.window, modal=True)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "OK", Gtk.ResponseType.OK)
        box = dialog.get_content_area(); box.set_spacing(6); box.set_border_width(8)
        label = Gtk.Label(label=prompt); label.set_xalign(0); box.pack_start(label, False, False, 0)
        entry = Gtk.Entry(); entry.set_text(initial); box.pack_start(entry, False, False, 0)
        dialog.show_all(); response = dialog.run(); value = entry.get_text().strip(); dialog.destroy()
        return value if response == Gtk.ResponseType.OK else None

    def choose_file(self, *, title="Select artifact", save=False, suffix=None):
        action = Gtk.FileChooserAction.SAVE if save else Gtk.FileChooserAction.OPEN
        dialog = Gtk.FileChooserDialog(title=title, transient_for=self.window, action=action)
        dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Save" if save else "Open", Gtk.ResponseType.OK)
        if save:
            dialog.set_do_overwrite_confirmation(True)
        if suffix:
            filt = Gtk.FileFilter(); filt.set_name("Automation Harness artifact")
            filt.add_pattern("*" + suffix); dialog.add_filter(filt)
            if save:
                dialog.set_current_name("untitled" + suffix)
        response = dialog.run(); filename = dialog.get_filename() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if not filename:
            return None
        path = Path(filename)
        if save and suffix and not path.name.casefold().endswith(suffix.casefold()):
            path = path.with_name(path.name + suffix)
        return path

    def open_artifact(self, path, *, project_context=None):
        if self.opener is None:
            return None
        return self.opener(Path(path), project_context=project_context or self.project_context)

    def save(self):
        raise NotImplementedError

    def _delete_event(self, *_args):
        if self.dirty and not self.confirm("Unsaved changes", "Close without saving changes?"):
            return True
        self.window.destroy()
        return False
