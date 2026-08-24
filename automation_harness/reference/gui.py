from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from automation_harness.reference.state import ReferenceState

TRACK_FILL = "#1e90ff"
FOLLOWED_TRACK_FILL = "#ff8c00"


class ReferenceGui:
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
