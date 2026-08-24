from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Track:
    track_id: str
    x: float
    y: float
    vx: float
    vy: float
    followed: bool = False
    visible: bool = True

    def advance(self, dt: float) -> None:
        self.x += self.vx * dt
        self.y += self.vy * dt
        # The synthetic field is normalized to 0..100 and wraps so long-running
        # tests remain deterministic and visible.
        self.x %= 100.0
        self.y %= 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "x": self.x,
            "y": self.y,
            "vx": self.vx,
            "vy": self.vy,
            "followed": self.followed,
            "visible": self.visible,
        }


@dataclass
class UiComponentState:
    component_id: str
    present: bool = True
    visible: bool = True
    showing: bool = True
    enabled: bool = True
    focused: bool = False
    selected: bool = False
    checked: bool = False
    pressed: bool = False
    expanded: bool = False
    editable: bool = False
    readonly: bool = True
    active: bool = False
    sensitive: bool = True
    bounds: tuple[int, int, int, int] | None = None
    text: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "present": self.present,
            "visible": self.visible,
            "showing": self.showing,
            "enabled": self.enabled,
            "focused": self.focused,
            "selected": self.selected,
            "checked": self.checked,
            "pressed": self.pressed,
            "expanded": self.expanded,
            "editable": self.editable,
            "readonly": self.readonly,
            "active": self.active,
            "sensitive": self.sensitive,
            "bounds": list(self.bounds) if self.bounds else None,
            "text": self.text,
        }


@dataclass
class ReferenceState:
    threat_level: str = "LOW"
    mosaic_tiles: list[str] = field(default_factory=list)
    tracks: dict[str, Track] = field(default_factory=dict)
    ui_ready: bool = False
    ui_components: dict[str, UiComponentState] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)
    _last_tick: float = field(default_factory=time.monotonic, repr=False)

    def reset(self) -> None:
        with self._lock:
            self.threat_level = "LOW"
            self.mosaic_tiles.clear()
            self.tracks.clear()
            self._last_tick = time.monotonic()

    def set_ui_ready(self, ready: bool) -> None:
        with self._lock:
            self.ui_ready = ready

    def register_ui_component(
        self,
        component_id: str,
        *,
        bounds: tuple[int, int, int, int] | None = None,
        text: str | None = None,
        enabled: bool = True,
        present: bool = True,
        **states: bool,
    ) -> None:
        with self._lock:
            existing = self.ui_components.get(component_id)
            if existing is None:
                self.ui_components[component_id] = UiComponentState(
                    component_id=component_id,
                    bounds=bounds,
                    text=text,
                    enabled=enabled,
                    present=present,
                    **{key: bool(value) for key, value in states.items() if key in {
                        "visible", "showing", "focused", "selected", "checked", "pressed",
                        "expanded", "editable", "readonly", "active", "sensitive"
                    }},
                )
            else:
                existing.bounds = bounds
                existing.text = text
                existing.enabled = enabled
                existing.present = present
                for key, value in states.items():
                    if hasattr(existing, key):
                        setattr(existing, key, bool(value))

    def tick(self) -> None:
        now = time.monotonic()
        with self._lock:
            dt = max(0.0, now - self._last_tick)
            self._last_tick = now
            for track in self.tracks.values():
                track.advance(dt)

    def handle(self, action: str, request: dict[str, Any]) -> Any:
        self.tick()
        with self._lock:
            if action == "health":
                return {
                    "status": "ok",
                    "tracks": len(self.tracks),
                    "ui_ready": self.ui_ready,
                    "ui_components": len(self.ui_components),
                }
            if action == "reset":
                self.reset()
                return {"status": "reset"}
            if action == "state":
                return self.snapshot()
            if action == "set_threat":
                return self._set_threat(str(request["level"]))
            if action == "add_tile":
                return self._add_tile(str(request["tile"]))
            if action == "remove_tile":
                return self._remove_tile(str(request["tile"]))
            if action == "clear_tiles":
                self.mosaic_tiles.clear()
                return list(self.mosaic_tiles)
            if action == "create_track":
                track_id = str(request["track_id"])
                self.tracks[track_id] = Track(
                    track_id=track_id,
                    x=float(request.get("x", 0.0)),
                    y=float(request.get("y", 0.0)),
                    vx=float(request.get("vx", 0.0)),
                    vy=float(request.get("vy", 0.0)),
                )
                return self.tracks[track_id].as_dict()
            if action == "get_track":
                return self._track(str(request["track_id"])).as_dict()
            if action == "follow_track":
                track_id = str(request["track_id"])
                for track in self.tracks.values():
                    track.followed = track.track_id == track_id
                return self._track(track_id).as_dict()
            if action == "set_track_visible":
                track = self._track(str(request["track_id"]))
                track.visible = bool(request["visible"])
                return track.as_dict()
            if action == "triangulate":
                points = request.get("points")
                if not isinstance(points, list) or len(points) < 2:
                    raise ValueError("triangulate requires at least two points")
                xs = [float(point[0]) for point in points]
                ys = [float(point[1]) for point in points]
                x = sum(xs) / len(xs)
                y = sum(ys) / len(ys)
                spread = math.sqrt(sum((px - x) ** 2 + (py - y) ** 2 for px, py in zip(xs, ys)) / len(points))
                return {"x": x, "y": y, "spread": spread}
            if action == "set_ui_component_state":
                component_id = str(request["component_id"])
                component = self.ui_components.get(component_id)
                if component is None:
                    raise ValueError(f"unknown UI component: {component_id}")
                allowed = {
                    "present", "visible", "showing", "enabled", "focused", "selected", "checked",
                    "pressed", "expanded", "editable", "readonly", "active", "sensitive"
                }
                for key, value in request.items():
                    if key in allowed:
                        setattr(component, key, bool(value))
                return component.as_dict()
            if action == "ui_component":
                component_id = str(request["component_id"])
                component = self.ui_components.get(component_id)
                if component is None:
                    return {"component_id": component_id, "present": False}
                return component.as_dict()
            if action == "ui_components":
                return {key: value.as_dict() for key, value in self.ui_components.items()}
            raise ValueError(f"unknown action: {action}")

    def _set_threat(self, raw_level: str) -> dict[str, Any]:
        level = raw_level.upper()
        if level not in {"LOW", "MEDIUM", "HIGH"}:
            raise ValueError(f"invalid threat level: {level}")
        self.threat_level = level
        return {"threat_level": level}

    def _add_tile(self, tile: str) -> list[str]:
        if tile not in self.mosaic_tiles:
            self.mosaic_tiles.append(tile)
        return list(self.mosaic_tiles)

    def _remove_tile(self, tile: str) -> list[str]:
        if tile in self.mosaic_tiles:
            self.mosaic_tiles.remove(tile)
        return list(self.mosaic_tiles)

    def _track(self, track_id: str) -> Track:
        try:
            return self.tracks[track_id]
        except KeyError as exc:
            raise ValueError(f"unknown track: {track_id}") from exc

    def snapshot(self) -> dict[str, Any]:
        return {
            "threat_level": self.threat_level,
            "mosaic_tiles": list(self.mosaic_tiles),
            "tracks": {track_id: track.as_dict() for track_id, track in self.tracks.items()},
            "ui_ready": self.ui_ready,
            "ui_components": {key: value.as_dict() for key, value in self.ui_components.items()},
        }
