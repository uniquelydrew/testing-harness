from __future__ import annotations

import json
import socket
import threading
from pathlib import Path

import pytest

from automation_harness.drivers.javafx_bridge import JavaFxBridgeDriver, discover_javafx_endpoints


_NODE = {
    "ref": "n17",
    "class": "javafx.scene.control.Button",
    "simple_class": "Button",
    "id": "cameraSelectorButton",
    "accessible_role": "BUTTON",
    "accessible_text": "Select Camera",
    "accessible_help": None,
    "visible": True,
    "disabled": False,
    "focused": False,
    "managed": True,
    "focus_traversable": True,
    "window": "ERSA Main Video Display",
    "style_classes": ["button"],
    "text": "Select Camera",
    "bounds": [100.2, 50.8, 120.0, 28.0],
    "hierarchy": ["AnchorPane", "GridPane#topControlBox", "Button#cameraSelectorButton"],
    "stable_ancestors": [
        {"id": "topControlBox", "class": "javafx.scene.layout.GridPane"},
    ],
    "user_data": None,
    "properties": {},
    "layout": {"grid_row": 0, "grid_column": 2, "grid_row_span": 1, "grid_column_span": 1},
    "sibling_index": 2,
    "sibling_count": 4,
    "actions": ["activate", "click", "get_text"],
    "parent": {
        "ref": "n4",
        "class": "javafx.scene.layout.GridPane",
        "simple_class": "GridPane",
        "id": "topControlBox",
        "accessible_role": "PARENT",
        "accessible_text": None,
        "text": None,
    },
}


class _BridgeServer:
    def __init__(self, node=None, find_matches=None):
        self.token = "test-token"
        self.node = dict(node or _NODE)
        self.find_matches = [dict(item) for item in find_matches] if find_matches is not None else None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(10)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        try:
            socket.create_connection(("127.0.0.1", self.port), timeout=0.2).close()
        except OSError:
            pass
        self.thread.join(timeout=1)
        self.sock.close()

    def _run(self):
        while not self.stop.is_set():
            try:
                client, _address = self.sock.accept()
            except OSError:
                return
            with client:
                raw = client.makefile("rb").readline()
                if not raw:
                    continue
                request = json.loads(raw.decode("utf-8"))
                response = self._response(request)
                client.sendall((json.dumps(response) + "\n").encode("utf-8"))

    def _response(self, request):
        if request.get("token") != self.token:
            return {"ok": False, "error": "invalid token"}
        op = request.get("op")
        base = {"ok": True, "protocol": "automation-harness-javafx/1", "pid": 1234}
        if op == "ping":
            return {**base, "javafx_available": True, "windows": 1}
        if op == "windows":
            return {**base, "windows": [{"title": self.node["window"], "showing": True}]}
        if op == "capture_next_click":
            return {**base, "node": self.node}
        if op == "hit_test":
            return {**base, "node": self.node}
        if op == "find":
            matches = list(self.find_matches) if self.find_matches is not None else [self.node]
            identification = request.get("identification") or {}
            mandatory = identification.get("mandatory", identification)
            return {
                **base,
                "matches": matches,
                "match_count": len(matches),
                "stages": [
                    {"source": "mandatory", "criteria": dict(mandatory), "matches": len(matches)}
                ],
            }
        if op == "activate":
            return {**base, "action": "fire", "node": self.node}
        if op == "get_text":
            return {**base, "text": self.node["text"]}
        if op == "set_text":
            updated = dict(self.node)
            updated["text"] = request.get("value")
            return {**base, "action": "set_text", "node": updated}
        return {"ok": False, "error": "unsupported op"}


def _write_discovery(directory: Path, server: _BridgeServer, pid=1234):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("javafx-%s.json" % pid)
    path.write_text(
        json.dumps({
            "protocol": "automation-harness-javafx/1",
            "pid": pid,
            "host": "127.0.0.1",
            "port": server.port,
            "token": server.token,
            "java_version": "21.0.7",
            "command": "ERSAMainVideoDisplay.jar",
        }),
        encoding="utf-8",
    )
    return path


def test_discovers_live_javafx_endpoint(tmp_path):
    server = _BridgeServer()
    try:
        _write_discovery(tmp_path, server)
        endpoints = discover_javafx_endpoints(tmp_path)
        assert len(endpoints) == 1
        assert endpoints[0].port == server.port
        assert endpoints[0].command == "ERSAMainVideoDisplay.jar"
    finally:
        server.close()


def test_capture_builds_durable_javafx_strategy(tmp_path):
    server = _BridgeServer()
    try:
        _write_discovery(tmp_path, server)
        captured = JavaFxBridgeDriver(discovery_dir=tmp_path).capture_next_click(timeout=1)
        assert captured.framework == "javafx"
        assert captured.accessible_id == "cameraSelectorButton"
        assert captured.native_class == "javafx.scene.control.Button"
        assert captured.bounds == (100, 51, 120, 28)
        strategy = captured.candidate_strategy()
        assert strategy.type == "javafx"
        identity = strategy.options["identification"]
        assert identity["mandatory"] == {"id": "cameraSelectorButton"}
        assert identity["assistive"]["window"] == "ERSA Main Video Display"
        assert identity["assistive"]["layout"]["grid_column"] == 2
        assert identity["assistive"]["lineage"] == [
            {"id": "topControlBox", "class": "javafx.scene.layout.GridPane"},
        ]
        assert "hierarchy" not in identity["assistive"]
        assert captured.backend_properties["sibling_index"] == 2
    finally:
        server.close()


def test_application_authored_node_property_outranks_generic_class(tmp_path):
    panel = dict(_NODE)
    panel.update({
        "ref": "n42",
        "class": "edu.mit.ll.ersa.common.dashboard.components.VideoPlayerFXMLController",
        "simple_class": "VideoPlayerFXMLController",
        "id": None,
        "accessible_role": "PARENT",
        "accessible_text": None,
        "text": None,
        "user_data": None,
        "properties": {"automation.feed-id": "camera-12", "javafx-internal": "ignored"},
        "layout": {"grid_row": 1, "grid_column": 3, "grid_row_span": 1, "grid_column_span": 1},
        "stable_ancestors": [{"id": "bigGridPane", "class": "javafx.scene.layout.GridPane"}],
    })
    server = _BridgeServer(node=panel)
    try:
        _write_discovery(tmp_path, server)
        captured = JavaFxBridgeDriver(discovery_dir=tmp_path).capture_next_click(timeout=1)
        identity = captured.candidate_strategy().options["identification"]
        assert identity["mandatory"] == {"properties": {"automation.feed-id": "camera-12"}}
        assert identity["assistive"]["class"] == panel["class"]
        assert identity["assistive"]["layout"]["grid_row"] == 1
        assert identity["assistive"]["lineage"][0]["id"] == "bigGridPane"
        assert captured.backend_properties["node_properties"]["automation.feed-id"] == "camera-12"
    finally:
        server.close()


def test_user_data_is_used_before_class_when_no_explicit_id_or_text(tmp_path):
    panel = dict(_NODE)
    panel.update({
        "ref": "n43",
        "class": "edu.mit.ll.ersa.FeedPanel",
        "simple_class": "FeedPanel",
        "id": None,
        "accessible_role": "PARENT",
        "accessible_text": None,
        "text": None,
        "user_data": "camera-7",
        "properties": {},
    })
    server = _BridgeServer(node=panel)
    try:
        _write_discovery(tmp_path, server)
        captured = JavaFxBridgeDriver(discovery_dir=tmp_path).capture_next_click(timeout=1)
        identity = captured.candidate_strategy().options["identification"]
        assert identity["mandatory"] == {"user_data": "camera-7", "class": "edu.mit.ll.ersa.FeedPanel"}
    finally:
        server.close()


def test_capture_infers_ordinal_only_for_still_ambiguous_javafx_node(tmp_path):
    target = {
        "ref": "n22",
        "class": "com.sun.javafx.scene.control.MenuBarButton",
        "simple_class": "MenuBarButton",
        "id": None,
        "accessible_role": "MENU",
        "accessible_text": None,
        "accessible_help": None,
        "visible": True,
        "disabled": False,
        "focused": False,
        "managed": True,
        "focus_traversable": False,
        "window": "ERSA Main Video Display",
        "style_classes": ["menu-button", "menu"],
        "text": None,
        "bounds": [1561.0, 65.0, 30.0, 27.0],
        "hierarchy": ["AnchorPane#AnchorPane", "MenuBar#topMenuBar", "HBox", "MenuBarButton"],
        "stable_ancestors": [
            {"id": "AnchorPane", "class": "javafx.scene.layout.AnchorPane"},
            {"id": "topMenuBar", "class": "javafx.scene.control.MenuBar"},
        ],
        "user_data": None,
        "properties": {},
        "layout": {},
        "sibling_index": 5,
        "sibling_count": 6,
        "actions": ["activate", "click"],
        "parent": {
            "ref": "n15",
            "class": "javafx.scene.layout.HBox",
            "simple_class": "HBox",
            "id": None,
            "accessible_role": "PARENT",
            "accessible_text": None,
            "text": None,
        },
    }
    peer = dict(target)
    peer["ref"] = "n19"
    peer["bounds"] = [1529.0, 65.0, 30.0, 27.0]
    server = _BridgeServer(node=target, find_matches=[peer, target])
    try:
        _write_discovery(tmp_path, server)
        captured = JavaFxBridgeDriver(discovery_dir=tmp_path).capture_next_click(timeout=1)
        identity = captured.candidate_strategy().options["identification"]
        assert identity["mandatory"] == {"accessible_role": "MENU"}
        assert identity["assistive"]["class"] == "com.sun.javafx.scene.control.MenuBarButton"
        assert identity["assistive"]["lineage"][1]["id"] == "topMenuBar"
        assert identity["ordinal"] == 1
    finally:
        server.close()


def test_resolve_and_semantic_operations_use_same_bridge(tmp_path):
    server = _BridgeServer()
    try:
        _write_discovery(tmp_path, server)
        driver = JavaFxBridgeDriver(discovery_dir=tmp_path)
        identity = {"mandatory": {"id": "cameraSelectorButton"}}
        resolved = driver.resolve("camera_selector", identification=identity)
        assert resolved.strategy == "javafx"
        assert resolved.metadata["framework"] == "javafx"
        assert driver.get_text(identification=identity) == "Select Camera"
        assert driver.activate(identification=identity)["action"] == "activate"
    finally:
        server.close()


def test_stale_discovery_record_is_ignored(tmp_path):
    path = tmp_path / "javafx-999.json"
    path.write_text(
        json.dumps({
            "pid": 999,
            "host": "127.0.0.1",
            "port": 9,
            "token": "dead",
        }),
        encoding="utf-8",
    )
    assert discover_javafx_endpoints(tmp_path) == ()
