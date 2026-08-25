from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from automation_harness.reference.state import ReferenceState

TRACK_FILL = "#1e90ff"
FOLLOWED_TRACK_FILL = "#ff8c00"


class TkReferenceGui:
    """Small synthetic desktop target that exercises automation mechanics only."""

    def __init__(self, state: ReferenceState) -> None:
        self.state = state
        self.root = tk.Tk()
        self.root.title("Automation Harness Reference Target")
        self.root.geometry("900x640+40+40")
        self.root.minsize(760, 520)
        self._build()
        self.root.update_idletasks()
        self._register_components()
        self.state.set_ui_ready(True)
        self.root.after(40, self._refresh)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        title = ttk.Label(outer, text="Synthetic Automation Reference Target", font=("Sans", 16, "bold"))
        title.pack(anchor="w")
        ttk.Label(
            outer,
            text="Contains no protected-environment UI, data, service definitions, or operational behavior.",
        ).pack(anchor="w", pady=(0, 10))

        controls = ttk.Frame(outer)
        controls.pack(fill="x")

        threat_box = ttk.LabelFrame(controls, text="Threat State", padding=8)
        threat_box.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.threat_label = ttk.Label(threat_box, text="LOW", font=("Sans", 13, "bold"))
        self.threat_label.pack(anchor="w", pady=(0, 6))
        threat_buttons = ttk.Frame(threat_box)
        threat_buttons.pack(anchor="w")
        self.low_button = ttk.Button(threat_buttons, text="Low", command=lambda: self.state.handle("set_threat", {"level": "LOW"}))
        self.medium_button = ttk.Button(threat_buttons, text="Medium", command=lambda: self.state.handle("set_threat", {"level": "MEDIUM"}))
        self.high_button = ttk.Button(threat_buttons, text="High", command=lambda: self.state.handle("set_threat", {"level": "HIGH"}))
        for button in (self.low_button, self.medium_button, self.high_button):
            button.pack(side="left", padx=(0, 6))

        mosaic_box = ttk.LabelFrame(controls, text="Mosaic", padding=8)
        mosaic_box.pack(side="left", fill="both", expand=True, padx=(6, 0))
        tile_buttons = ttk.Frame(mosaic_box)
        tile_buttons.pack(anchor="w")
        self.alpha_button = ttk.Button(tile_buttons, text="Add Alpha", command=lambda: self.state.handle("add_tile", {"tile": "Alpha"}))
        self.bravo_button = ttk.Button(tile_buttons, text="Add Bravo", command=lambda: self.state.handle("add_tile", {"tile": "Bravo"}))
        self.clear_button = ttk.Button(tile_buttons, text="Clear Tiles", command=lambda: self.state.handle("clear_tiles", {}))
        for button in (self.alpha_button, self.bravo_button, self.clear_button):
            button.pack(side="left", padx=(0, 6))
        self.tile_label = ttk.Label(mosaic_box, text="(none)")
        self.tile_label.pack(anchor="w", pady=(8, 0))

        state_box = ttk.LabelFrame(outer, text="Object State Laboratory", padding=8)
        state_box.pack(fill="x", pady=(12, 0))
        self.options_button = ttk.Button(state_box, text="Options Menu", command=self._toggle_options_popup)
        self.options_button.pack(side="left", padx=(0, 6))
        self.disabled_button = ttk.Button(state_box, text="Disabled Action", state="disabled")
        self.disabled_button.pack(side="left")
        self.options_popup = None

        canvas_box = ttk.LabelFrame(outer, text="Synthetic Moving Target Field", padding=8)
        canvas_box.pack(fill="both", expand=True, pady=(12, 0))
        self.canvas = tk.Canvas(canvas_box, background="#101820", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._register_components())

    def _widget_bounds(self, widget: tk.Widget) -> tuple[int, int, int, int]:
        self.root.update_idletasks()
        return (widget.winfo_rootx(), widget.winfo_rooty(), widget.winfo_width(), widget.winfo_height())

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
            try:
                bounds = self._widget_bounds(widget)
            except tk.TclError:
                continue
            enabled = "disabled" not in widget.state() if isinstance(widget, ttk.Widget) else True
            self.state.register_ui_component(component_id, bounds=bounds, text=text, enabled=enabled)
        popup_present = self.options_popup is not None and bool(self.options_popup.winfo_exists())
        self.state.register_ui_component(
            "state.options_popup",
            bounds=self._widget_bounds(self.options_popup) if popup_present else None,
            text="Options Popup",
            present=popup_present,
            visible=popup_present,
            showing=popup_present,
        )

    def _toggle_options_popup(self) -> None:
        if self.options_popup is not None and self.options_popup.winfo_exists():
            self.options_popup.destroy()
            self.options_popup = None
            self.state.handle("set_ui_component_state", {"component_id": "state.options_menu", "expanded": False})
            return
        popup = tk.Toplevel(self.root)
        popup.title("Options")
        popup.geometry("220x110+260+250")
        ttk.Label(popup, text="Synthetic menu content").pack(padx=12, pady=12)
        ttk.Button(popup, text="Close", command=self._toggle_options_popup).pack()
        popup.protocol("WM_DELETE_WINDOW", self._toggle_options_popup)
        self.options_popup = popup
        self.state.handle("set_ui_component_state", {"component_id": "state.options_menu", "expanded": True})
        self._register_components()

    def _refresh(self) -> None:
        if not self.root.winfo_exists():
            return
        self.state.tick()
        snapshot = self.state.snapshot()
        self.threat_label.configure(text=snapshot["threat_level"])
        tiles = snapshot["mosaic_tiles"]
        self.tile_label.configure(text=", ".join(tiles) if tiles else "(none)")
        self._draw_tracks(snapshot["tracks"])
        self._register_components()
        self.root.after(40, self._refresh)

    def _draw_tracks(self, tracks: dict[str, dict]) -> None:
        self.canvas.delete("track")
        width = max(self.canvas.winfo_width(), 1)
        height = max(self.canvas.winfo_height(), 1)
        margin = 18
        usable_w = max(1, width - 2 * margin)
        usable_h = max(1, height - 2 * margin)
        for track_id, track in tracks.items():
            if not track["visible"]:
                continue
            x = margin + float(track["x"]) / 100.0 * usable_w
            y = margin + float(track["y"]) / 100.0 * usable_h
            radius = 9
            fill = FOLLOWED_TRACK_FILL if track["followed"] else TRACK_FILL
            self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=fill, outline="white", width=2, tags=("track",))
            self.canvas.create_text(x + 14, y - 12, text=track_id, fill="white", anchor="w", tags=("track",))

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            self.state.set_ui_ready(False)

    def close(self) -> None:
        try:
            self.root.after(0, self.root.destroy)
        except tk.TclError:
            pass


try:
    import cairo  # noqa: F401  # Registers the Cairo foreign-struct converter for Gtk.DrawingArea.
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk
except (ImportError, ValueError):  # Keep the reference target usable on non-Linux hosts.
    Gtk = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]


if Gtk is not None:

    class GtkReferenceGui:
        """AT-SPI-visible implementation of the synthetic reference desktop.

        GTK exposes buttons through the Linux accessibility bus, which lets the
        reference suite validate the same resolution and activation route that a
        real accessible application uses.  ``TkReferenceGui`` remains the
        portable fallback for hosts without PyGObject/GTK.
        """

        def __init__(self, state: ReferenceState) -> None:
            self.state = state
            self.window = Gtk.Window(title="Automation Harness Reference Target")
            self.window.set_default_size(900, 640)
            self.window.set_size_request(760, 520)
            self.window.connect("destroy", lambda *_args: Gtk.main_quit())
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
            description = Gtk.Label(
                label="Contains no protected-environment UI, data, service definitions, or operational behavior."
            )
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
            self.options_popup = None

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
            # PyGObject exposes ``get_origin`` as either ``(x, y)`` or
            # ``(success, x, y)`` depending on the GDK binding version.
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
                self.state.register_ui_component(
                    component_id,
                    bounds=self._widget_bounds(widget),
                    text=text,
                    enabled=widget.get_sensitive(),
                )
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
            popup = Gtk.Window(title="Options", transient_for=self.window)
            popup.set_default_size(220, 110)
            contents = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            contents.set_border_width(12)
            popup.add(contents)
            contents.pack_start(Gtk.Label(label="Synthetic menu content"), True, True, 0)
            close = Gtk.Button(label="Close")
            close.connect("clicked", self._toggle_options_popup)
            contents.pack_start(close, False, False, 0)
            self.options_popup = popup
            popup.show_all()
            self.state.handle("set_ui_component_state", {"component_id": "state.options_menu", "expanded": True})
            self._register_components()

        def _refresh(self) -> bool:
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
                context.set_source_rgb(*( (1.0, 140 / 255, 0.0) if track["followed"] else (30 / 255, 144 / 255, 1.0) ))
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


ReferenceGui = GtkReferenceGui if Gtk is not None else TkReferenceGui
