from __future__ import annotations

import cairo  # noqa: F401
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from automation_harness.reference.state import ReferenceState

TRACK_FILL = (30 / 255, 144 / 255, 1.0)
FOLLOWED_TRACK_FILL = (1.0, 140 / 255, 0.0)


class GtkReferenceGui:
    """AT-SPI-visible synthetic desktop implemented entirely with GTK3."""

    def __init__(self, state: ReferenceState) -> None:
        self.state = state
        self.window = Gtk.Window(title="Automation Harness Reference Target")
        self.window.set_default_size(900, 640)
        self.window.set_size_request(760, 520)
        self.window.connect("destroy", lambda *_args: Gtk.main_quit())
        self.options_popup = None
        self._build()
        self.window.show_all()
        self._register_components()
        self.state.set_ui_ready(True)
        GLib.timeout_add(40, self._refresh)

    def _build(self) -> None:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_border_width(12)
        self.window.add(outer)
        title = Gtk.Label()
        title.set_markup("<b><span size='x-large'>Synthetic Automation Reference Target</span></b>")
        title.set_halign(Gtk.Align.START)
        outer.pack_start(title, False, False, 0)
        description = Gtk.Label(label="Contains no protected-environment UI, data, service definitions, or operational behavior.")
        description.set_halign(Gtk.Align.START)
        outer.pack_start(description, False, False, 0)

        controls = Gtk.Box(spacing=12)
        outer.pack_start(controls, False, False, 0)
        threat_box = Gtk.Frame(label="Threat State")
        controls.pack_start(threat_box, True, True, 0)
        threat_contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        threat_contents.set_border_width(8)
        threat_box.add(threat_contents)
        self.threat_label = Gtk.Label(label="LOW")
        self.threat_label.set_halign(Gtk.Align.START)
        threat_contents.pack_start(self.threat_label, False, False, 0)
        threat_buttons = Gtk.Box(spacing=6)
        threat_contents.pack_start(threat_buttons, False, False, 0)
        self.low_button = Gtk.Button(label="Low")
        self.medium_button = Gtk.Button(label="Medium")
        self.high_button = Gtk.Button(label="High")
        self.low_button.connect("clicked", lambda *_args: self.state.handle("set_threat", {"level": "LOW"}))
        self.medium_button.connect("clicked", lambda *_args: self.state.handle("set_threat", {"level": "MEDIUM"}))
        self.high_button.connect("clicked", lambda *_args: self.state.handle("set_threat", {"level": "HIGH"}))
        for button in (self.low_button, self.medium_button, self.high_button):
            threat_buttons.pack_start(button, False, False, 0)

        mosaic_box = Gtk.Frame(label="Mosaic")
        controls.pack_start(mosaic_box, True, True, 0)
        mosaic_contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        mosaic_contents.set_border_width(8)
        mosaic_box.add(mosaic_contents)
        tile_buttons = Gtk.Box(spacing=6)
        mosaic_contents.pack_start(tile_buttons, False, False, 0)
        self.alpha_button = Gtk.Button(label="Add Alpha")
        self.bravo_button = Gtk.Button(label="Add Bravo")
        self.clear_button = Gtk.Button(label="Clear Tiles")
        self.alpha_button.connect("clicked", lambda *_args: self.state.handle("add_tile", {"tile": "Alpha"}))
        self.bravo_button.connect("clicked", lambda *_args: self.state.handle("add_tile", {"tile": "Bravo"}))
        self.clear_button.connect("clicked", lambda *_args: self.state.handle("clear_tiles", {}))
        for button in (self.alpha_button, self.bravo_button, self.clear_button):
            tile_buttons.pack_start(button, False, False, 0)
        self.tile_label = Gtk.Label(label="(none)")
        self.tile_label.set_halign(Gtk.Align.START)
        mosaic_contents.pack_start(self.tile_label, False, False, 0)

        state_box = Gtk.Frame(label="Object State Laboratory")
        outer.pack_start(state_box, False, False, 0)
        state_contents = Gtk.Box(spacing=6)
        state_contents.set_border_width(8)
        state_box.add(state_contents)
        self.options_button = Gtk.Button(label="Options Menu")
        self.options_button.connect("clicked", self._toggle_options_popup)
        state_contents.pack_start(self.options_button, False, False, 0)
        self.disabled_button = Gtk.Button(label="Disabled Action")
        self.disabled_button.set_sensitive(False)
        state_contents.pack_start(self.disabled_button, False, False, 0)

        canvas_box = Gtk.Frame(label="Synthetic Moving Target Field")
        outer.pack_start(canvas_box, True, True, 0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_size_request(400, 240)
        self.canvas.connect("draw", self._draw_tracks)
        canvas_box.add(self.canvas)

    def _widget_bounds(self, widget) -> tuple[int, int, int, int]:
        allocation = widget.get_allocation()
        window = widget.get_window()
        if window is None:
            return (0, 0, 0, 0)
        origin = window.get_origin()
        x, y = origin[-2:]
        return (int(x), int(y), int(allocation.width), int(allocation.height))

    def _register_components(self) -> None:
        mappings = {
            "threat.low": (self.low_button, "Low"),
            "threat.medium": (self.medium_button, "Medium"),
            "threat.high": (self.high_button, "High"),
            "mosaic.add_alpha": (self.alpha_button, "Add Alpha"),
            "mosaic.add_bravo": (self.bravo_button, "Add Bravo"),
            "mosaic.clear": (self.clear_button, "Clear Tiles"),
            "state.options_menu": (self.options_button, "Options Menu"),
            "state.disabled_action": (self.disabled_button, "Disabled Action"),
            "track.canvas": (self.canvas, None),
        }
        for component_id, (widget, text) in mappings.items():
            self.state.register_ui_component(component_id, bounds=self._widget_bounds(widget), text=text, enabled=widget.get_sensitive())
        popup_present = self.options_popup is not None and self.options_popup.get_visible()
        self.state.register_ui_component(
            "state.options_popup",
            bounds=self._widget_bounds(self.options_popup) if popup_present else None,
            text="Options Popup",
            present=popup_present,
            visible=popup_present,
            showing=popup_present,
        )

    def _toggle_options_popup(self, *_args) -> None:
        if self.options_popup is not None and self.options_popup.get_visible():
            self.options_popup.destroy()
            self.options_popup = None
            self.state.handle("set_ui_component_state", {"component_id": "state.options_menu", "expanded": False})
            return
        popup = Gtk.Window(title="Options")
        popup.set_default_size(220, 110)
        popup.set_transient_for(self.window)
        popup.connect("delete-event", self._close_popup)
        contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        contents.set_border_width(12)
        popup.add(contents)
        contents.pack_start(Gtk.Label(label="Synthetic menu content"), True, True, 0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda *_args: self._toggle_options_popup())
        contents.pack_start(close, False, False, 0)
        self.options_popup = popup
        popup.show_all()
        self.state.handle("set_ui_component_state", {"component_id": "state.options_menu", "expanded": True})
        self._register_components()

    def _close_popup(self, *_args):
        self._toggle_options_popup()
        return True

    def _refresh(self) -> bool:
        if not self.window.get_visible():
            return False
        self.state.tick()
        snapshot = self.state.snapshot()
        self.threat_label.set_text(snapshot["threat_level"])
        tiles = snapshot["mosaic_tiles"]
        self.tile_label.set_text(", ".join(tiles) if tiles else "(none)")
        self.canvas.queue_draw()
        self._register_components()
        return True

    def _draw_tracks(self, widget, context) -> bool:
        allocation = widget.get_allocation()
        width, height = max(allocation.width, 1), max(allocation.height, 1)
        context.set_source_rgb(16 / 255, 24 / 255, 32 / 255)
        context.paint()
        margin = 18
        for track_id, track in self.state.snapshot()["tracks"].items():
            if not track["visible"]:
                continue
            x = margin + float(track["x"]) / 100.0 * max(1, width - 2 * margin)
            y = margin + float(track["y"]) / 100.0 * max(1, height - 2 * margin)
            context.set_source_rgb(*(FOLLOWED_TRACK_FILL if track["followed"] else TRACK_FILL))
            context.arc(x, y, 9, 0, 6.283185307179586)
            context.fill_preserve()
            context.set_source_rgb(1, 1, 1)
            context.set_line_width(2)
            context.stroke()
            context.move_to(x + 14, y - 12)
            context.show_text(track_id)
        return False

    def run(self) -> None:
        Gtk.main()
        self.state.set_ui_ready(False)

    def close(self) -> None:
        if self.window.get_visible():
            self.window.destroy()


ReferenceGui = GtkReferenceGui
