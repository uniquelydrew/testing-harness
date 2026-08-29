from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk


_JAVAFX_KNOWN_CLASSES = (
    "javafx.scene.Group",
    "javafx.scene.SubScene",
    "javafx.scene.canvas.Canvas",
    "javafx.scene.image.ImageView",
    "javafx.scene.layout.AnchorPane",
    "javafx.scene.layout.BorderPane",
    "javafx.scene.layout.FlowPane",
    "javafx.scene.layout.GridPane",
    "javafx.scene.layout.HBox",
    "javafx.scene.layout.Pane",
    "javafx.scene.layout.Region",
    "javafx.scene.layout.StackPane",
    "javafx.scene.layout.TilePane",
    "javafx.scene.layout.VBox",
    "javafx.scene.control.Accordion",
    "javafx.scene.control.Button",
    "javafx.scene.control.ButtonBase",
    "javafx.scene.control.CheckBox",
    "javafx.scene.control.ChoiceBox",
    "javafx.scene.control.ColorPicker",
    "javafx.scene.control.ComboBox",
    "javafx.scene.control.ComboBoxBase",
    "javafx.scene.control.DatePicker",
    "javafx.scene.control.Hyperlink",
    "javafx.scene.control.Label",
    "javafx.scene.control.Labeled",
    "javafx.scene.control.ListCell",
    "javafx.scene.control.ListView",
    "javafx.scene.control.MenuBar",
    "javafx.scene.control.MenuButton",
    "javafx.scene.control.Pagination",
    "javafx.scene.control.PasswordField",
    "javafx.scene.control.ProgressBar",
    "javafx.scene.control.ProgressIndicator",
    "javafx.scene.control.RadioButton",
    "javafx.scene.control.ScrollBar",
    "javafx.scene.control.ScrollPane",
    "javafx.scene.control.Separator",
    "javafx.scene.control.Slider",
    "javafx.scene.control.Spinner",
    "javafx.scene.control.SplitMenuButton",
    "javafx.scene.control.SplitPane",
    "javafx.scene.control.TabPane",
    "javafx.scene.control.TableCell",
    "javafx.scene.control.TableRow",
    "javafx.scene.control.TableView",
    "javafx.scene.control.TextArea",
    "javafx.scene.control.TextField",
    "javafx.scene.control.TextInputControl",
    "javafx.scene.control.TitledPane",
    "javafx.scene.control.ToggleButton",
    "javafx.scene.control.ToolBar",
    "javafx.scene.control.TreeCell",
    "javafx.scene.control.TreeTableCell",
    "javafx.scene.control.TreeTableRow",
    "javafx.scene.control.TreeTableView",
    "javafx.scene.control.TreeView",
    "javafx.scene.media.MediaView",
    "javafx.scene.shape.Circle",
    "javafx.scene.shape.Ellipse",
    "javafx.scene.shape.Line",
    "javafx.scene.shape.Path",
    "javafx.scene.shape.Polygon",
    "javafx.scene.shape.Polyline",
    "javafx.scene.shape.Rectangle",
    "javafx.scene.shape.Shape",
    "javafx.scene.text.Text",
    "javafx.scene.web.WebView",
)


class IdentityEditorDialog:
    """GTK form editor for progressive object identity.

    Identity keys are never edited as free-form serialized text. Each condition
    is represented as a key=value pair with an explicit enable toggle. Nested
    mappings and ordered lists are rendered as subordinate key=value rows while
    retaining the original identity structure.
    """

    def __init__(self, parent, identification: Mapping[str, Any], *, framework: str | None = None):
        self.original = _normalize_identity(identification)
        self.framework = framework or "object"
        self.known_classes = _known_classes(self.framework, self.original)
        self._conditions = {"mandatory": [], "assistive": []}
        self._ordinal_enabled = None
        self._ordinal = None

        self.dialog = Gtk.Dialog(
            title="Object Identification",
            transient_for=parent,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        self.dialog.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Use Identity", Gtk.ResponseType.OK,
        )
        self.dialog.set_default_size(820, 580)
        self.dialog.set_resizable(True)
        self.dialog.set_default_response(Gtk.ResponseType.OK)

        content = self.dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(10)

        title = Gtk.Label()
        title.set_markup("<b>%s identity</b>" % _escape(self.framework.upper()))
        title.set_halign(Gtk.Align.START)
        content.pack_start(title, False, False, 0)

        explanation = Gtk.Label(
            label=(
                "Identity is stored as structured key=value conditions. "
                "Keys are fixed; edit only the values or disable conditions that should not participate. "
                "Class fields offer known framework/runtime classes and also accept a custom class."
            )
        )
        explanation.set_line_wrap(True)
        explanation.set_halign(Gtk.Align.START)
        content.pack_start(explanation, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        content.pack_start(scroll, True, True, 0)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        body.set_border_width(4)
        scroll.add(body)

        self._build_scope(body, "mandatory", "Mandatory conditions")
        self._build_scope(body, "assistive", "Assistive conditions")
        self._build_ordinal(body)

        self.error = Gtk.Label()
        self.error.set_halign(Gtk.Align.START)
        self.error.set_line_wrap(True)
        content.pack_start(self.error, False, False, 0)

        self.dialog.show_all()

    def _build_scope(self, body, scope, caption):
        frame = Gtk.Frame(label=caption)
        body.pack_start(frame, False, False, 0)
        grid = Gtk.Grid()
        grid.set_border_width(8)
        grid.set_row_spacing(6)
        grid.set_column_spacing(8)
        frame.add(grid)

        header_use = Gtk.Label(label="Use")
        header_key = Gtk.Label(label="Key")
        header_value = Gtk.Label(label="Value")
        for widget in (header_use, header_key, header_value):
            widget.set_halign(Gtk.Align.START)
        grid.attach(header_use, 0, 0, 1, 1)
        grid.attach(header_key, 1, 0, 1, 1)
        grid.attach(header_value, 3, 0, 1, 1)

        values = self.original.get(scope, {})
        if not isinstance(values, Mapping) or not values:
            empty = Gtk.Label(label="No %s conditions." % scope)
            empty.set_halign(Gtk.Align.START)
            grid.attach(empty, 1, 1, 3, 1)
            return

        row = 1
        for key, value in values.items():
            condition = _ConditionEditor(
                grid,
                row,
                str(key),
                value,
                known_classes=self.known_classes,
            )
            self._conditions[scope].append(condition)
            row += condition.rows

    def _build_ordinal(self, body):
        frame = Gtk.Frame(label="Final discriminator")
        body.pack_start(frame, False, False, 0)
        grid = Gtk.Grid()
        grid.set_border_width(8)
        grid.set_row_spacing(6)
        grid.set_column_spacing(8)
        frame.add(grid)

        self._ordinal_enabled = Gtk.CheckButton()
        self._ordinal_enabled.set_active("ordinal" in self.original)
        grid.attach(self._ordinal_enabled, 0, 0, 1, 1)

        key = Gtk.Label(label="ordinal")
        key.set_halign(Gtk.Align.START)
        grid.attach(key, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="="), 2, 0, 1, 1)

        adjustment = Gtk.Adjustment(
            value=float(self.original.get("ordinal", 0) or 0),
            lower=0,
            upper=1000000,
            step_increment=1,
            page_increment=10,
            page_size=0,
        )
        self._ordinal = Gtk.SpinButton(adjustment=adjustment, climb_rate=1, digits=0)
        self._ordinal.set_hexpand(False)
        grid.attach(self._ordinal, 3, 0, 1, 1)

        note = Gtk.Label(label="Used only when stable key=value conditions still match multiple runtime objects.")
        note.set_halign(Gtk.Align.START)
        note.set_line_wrap(True)
        grid.attach(note, 1, 1, 3, 1)

    def run(self):
        while True:
            response = self.dialog.run()
            if response != Gtk.ResponseType.OK:
                self.dialog.destroy()
                return None
            try:
                value = self.value()
            except ValueError as exc:
                self.error.set_markup("<span foreground='red'>%s</span>" % _escape(str(exc)))
                continue
            self.dialog.destroy()
            return value

    def value(self):
        mandatory = {}
        assistive = {}
        for condition in self._conditions["mandatory"]:
            if condition.enabled():
                mandatory[condition.key] = condition.value()
        for condition in self._conditions["assistive"]:
            if condition.enabled():
                assistive[condition.key] = condition.value()

        if not mandatory:
            raise ValueError("At least one mandatory key=value condition must be enabled.")

        result = {"mandatory": mandatory}
        if assistive:
            result["assistive"] = assistive
        if self._ordinal_enabled.get_active():
            result["ordinal"] = int(self._ordinal.get_value_as_int())
        return result


class _ConditionEditor:
    def __init__(self, grid, start_row, key, value, *, known_classes=()):
        self.key = key
        self.original = value
        self.known_classes = tuple(known_classes)
        self.toggle = Gtk.CheckButton()
        self.toggle.set_active(True)
        grid.attach(self.toggle, 0, start_row, 1, 1)

        label = Gtk.Label(label=key)
        label.set_halign(Gtk.Align.START)
        grid.attach(label, 1, start_row, 1, 1)
        grid.attach(Gtk.Label(label="="), 2, start_row, 1, 1)

        leaves = list(_flatten(value))
        self.entries = []
        if len(leaves) == 1 and not leaves[0][0]:
            path, leaf = leaves[0]
            field = _field_for(key, path, leaf, self.known_classes)
            grid.attach(field.widget, 3, start_row, 1, 1)
            self.entries.append((path, leaf, field))
            self.rows = 1
        else:
            summary = Gtk.Label(label=_container_summary(value))
            summary.set_halign(Gtk.Align.START)
            grid.attach(summary, 3, start_row, 1, 1)
            row = start_row + 1
            for path, leaf in leaves:
                child_key = Gtk.Label(label="  %s" % _path_label(key, path))
                child_key.set_halign(Gtk.Align.START)
                grid.attach(child_key, 1, row, 1, 1)
                grid.attach(Gtk.Label(label="="), 2, row, 1, 1)
                field = _field_for(key, path, leaf, self.known_classes)
                grid.attach(field.widget, 3, row, 1, 1)
                self.entries.append((path, leaf, field))
                row += 1
            self.rows = max(1, row - start_row)

        self.toggle.connect("toggled", self._toggle_entries)
        self._toggle_entries()

    def _toggle_entries(self, *_args):
        enabled = self.toggle.get_active()
        for _path, _leaf, field in self.entries:
            field.set_sensitive(enabled)

    def enabled(self):
        return bool(self.toggle.get_active())

    def value(self):
        leaves = {}
        for path, original, field in self.entries:
            text = field.get_text().strip()
            if isinstance(original, str):
                if not text:
                    raise ValueError("%s%s requires a non-empty value." % (self.key, _suffix(path)))
                value = text
            elif isinstance(original, bool):
                lowered = text.casefold()
                if lowered not in {"true", "false"}:
                    raise ValueError("%s%s must be true or false." % (self.key, _suffix(path)))
                value = lowered == "true"
            elif isinstance(original, int) and not isinstance(original, bool):
                try:
                    value = int(text)
                except ValueError:
                    raise ValueError("%s%s must be an integer." % (self.key, _suffix(path)))
            elif isinstance(original, float):
                try:
                    value = float(text)
                except ValueError:
                    raise ValueError("%s%s must be numeric." % (self.key, _suffix(path)))
            elif original is None:
                value = text
            else:
                value = text
            leaves[path] = value
        return _rebuild(self.original, leaves)


class _Field:
    def __init__(self, widget, entry):
        self.widget = widget
        self.entry = entry

    def get_text(self):
        return self.entry.get_text()

    def set_sensitive(self, value):
        self.widget.set_sensitive(value)


def edit_identity(parent, identification: Mapping[str, Any], *, framework: str | None = None):
    """Open the structured identity form and return its mapping or None."""
    return IdentityEditorDialog(parent, identification, framework=framework).run()


def edit_locator(parent, fields):
    """Edit a small flat locator as key=value pairs without serialized text."""
    dialog = Gtk.Dialog(
        title="Capture by Locator",
        transient_for=parent,
        flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
    )
    dialog.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Capture", Gtk.ResponseType.OK)
    dialog.set_default_response(Gtk.ResponseType.OK)
    content = dialog.get_content_area()
    content.set_border_width(12)
    content.set_spacing(8)
    label = Gtk.Label(label="Enter one or more locator key=value pairs.")
    label.set_halign(Gtk.Align.START)
    content.pack_start(label, False, False, 0)
    grid = Gtk.Grid()
    grid.set_row_spacing(6)
    grid.set_column_spacing(8)
    content.pack_start(grid, False, False, 0)
    entries = {}
    for row, key in enumerate(fields):
        key_label = Gtk.Label(label=str(key))
        key_label.set_halign(Gtk.Align.START)
        grid.attach(key_label, 0, row, 1, 1)
        grid.attach(Gtk.Label(label="="), 1, row, 1, 1)
        entry = Gtk.Entry()
        entry.set_hexpand(True)
        grid.attach(entry, 2, row, 1, 1)
        entries[str(key)] = entry
    error = Gtk.Label()
    error.set_halign(Gtk.Align.START)
    content.pack_start(error, False, False, 0)
    dialog.show_all()
    while True:
        response = dialog.run()
        if response != Gtk.ResponseType.OK:
            dialog.destroy()
            return None
        result = {key: entry.get_text().strip() for key, entry in entries.items() if entry.get_text().strip()}
        if result:
            dialog.destroy()
            return result
        error.set_markup("<span foreground='red'>Enter at least one key=value pair.</span>")


def _normalize_identity(value):
    if not isinstance(value, Mapping):
        raise ValueError("identification must be a mapping")
    mandatory = value.get("mandatory", {})
    assistive = value.get("assistive", {})
    if not isinstance(mandatory, Mapping) or not mandatory:
        raise ValueError("identification requires at least one mandatory condition")
    if not isinstance(assistive, Mapping):
        raise ValueError("assistive identification conditions must be a mapping")
    result = {"mandatory": dict(mandatory)}
    if assistive:
        result["assistive"] = dict(assistive)
    if value.get("ordinal") is not None:
        ordinal = value.get("ordinal")
        if isinstance(ordinal, Mapping):
            ordinal = ordinal.get("index")
        if not isinstance(ordinal, int) or isinstance(ordinal, bool) or ordinal < 0:
            raise ValueError("identity ordinal must be a non-negative integer")
        result["ordinal"] = ordinal
    return result


def _known_classes(framework, identification):
    values = set()
    if str(framework).casefold() == "javafx":
        values.update(_JAVAFX_KNOWN_CLASSES)
        values.update(_discover_javafx_classes())
    _collect_identity_classes(identification, values)
    return tuple(sorted(value for value in values if value))


def _discover_javafx_classes():
    """Collect classes currently observed from all live JavaFX bridge trees."""
    values = set()
    try:
        from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver
        driver = JavaFxBridgeDriver()
        for endpoint in driver.endpoints():
            try:
                response = endpoint.request("tree", timeout=2.0, max_depth=64)
            except Exception:
                continue
            _collect_tree_classes(response, values)
    except Exception:
        pass
    return values


def _collect_tree_classes(value, target):
    if isinstance(value, Mapping):
        class_name = value.get("class")
        if isinstance(class_name, str) and class_name:
            target.add(class_name)
        for child in value.values():
            _collect_tree_classes(child, target)
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_tree_classes(child, target)


def _collect_identity_classes(value, target, key=None):
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            _collect_identity_classes(child, target, key=str(child_key))
    elif isinstance(value, (list, tuple)):
        for child in value:
            _collect_identity_classes(child, target, key=key)
    elif key == "class" and isinstance(value, str) and value:
        target.add(value)


def _flatten(value, path=()):
    if isinstance(value, Mapping):
        if not value:
            yield path, ""
            return
        for key, child in value.items():
            for item in _flatten(child, path + (str(key),)):
                yield item
        return
    if isinstance(value, (list, tuple)):
        if not value:
            yield path, ""
            return
        for index, child in enumerate(value):
            for item in _flatten(child, path + (index,)):
                yield item
        return
    yield path, value


def _rebuild(template, leaves, path=()):
    if isinstance(template, Mapping):
        return {key: _rebuild(value, leaves, path + (str(key),)) for key, value in template.items()}
    if isinstance(template, list):
        return [_rebuild(value, leaves, path + (index,)) for index, value in enumerate(template)]
    if isinstance(template, tuple):
        return tuple(_rebuild(value, leaves, path + (index,)) for index, value in enumerate(template))
    return leaves[path]


def _field_for(root_key, path, value, known_classes):
    if _is_class_key(root_key, path):
        combo = Gtk.ComboBoxText.new_with_entry()
        combo.set_hexpand(True)
        for class_name in known_classes:
            combo.append_text(class_name)
        entry = combo.get_child()
        entry.set_text("" if value is None else str(value))
        entry.set_placeholder_text("Choose a known class or type a custom class")
        return _Field(combo, entry)
    entry = Gtk.Entry()
    if isinstance(value, bool):
        entry.set_text("true" if value else "false")
    elif value is None:
        entry.set_text("")
    else:
        entry.set_text(str(value))
    entry.set_hexpand(True)
    return _Field(entry, entry)


def _is_class_key(root_key, path):
    if root_key == "class" and not path:
        return True
    return bool(path) and path[-1] == "class"


def _container_summary(value):
    if isinstance(value, Mapping):
        return "%d nested key=value pair(s)" % len(list(_flatten(value)))
    if isinstance(value, (list, tuple)):
        return "%d ordered value(s)" % len(value)
    return ""


def _path_label(root, path):
    value = root
    for token in path:
        if isinstance(token, int):
            value += "[%d]" % token
        else:
            value += "." + token
    return value


def _suffix(path):
    if not path:
        return ""
    return " (" + _path_label("", path).lstrip(".") + ")"


def _escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("'", "&apos;")
        .replace('"', "&quot;")
    )
