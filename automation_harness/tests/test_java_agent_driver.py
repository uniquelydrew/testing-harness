from automation_harness.drivers import java_agent
from automation_harness.drivers.java_agent import (
    JavaAgentDriver,
    configured_java_agent_transports,
    configured_java_recording_transports,
    discover_java_agent_transports,
)


class _Transport:
    def __init__(self, pid=None):
        self.calls = []
        self.pid = pid

    def request(self, operation, payload):
        self.calls.append((operation, payload))
        return {
            "semantic_node": {
                "framework": "swing", "class": "com.jogamp.opengl.awt.GLCanvas",
                "native_class": "com.jogamp.opengl.awt.GLCanvas", "name": "Display",
                "accessible_id": "display", "window": "MSCT", "role": "canvas",
                "bounds": [10, 20, 800, 600], "properties": {"opaque_render_surface": True},
            }
        }


def test_java_agent_resolves_swing_locator_without_atspi(monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    driver = JavaAgentDriver()
    transport = _Transport()
    driver.refresh_transports = lambda: (transport,)

    capture = driver.inspect(identification={
        "mandatory": {"accessible_id": "display"},
        "assistive": {"native_class": "com.jogamp.opengl.awt.GLCanvas", "window": "MSCT"},
    })

    assert capture.bounds == (10, 20, 800, 600)
    assert capture.framework == "swing"
    assert transport.calls == [("resolve", {
        "accessible_id": "display",
        "native_class": "com.jogamp.opengl.awt.GLCanvas",
        "window": "MSCT",
    })]


def test_java_agent_point_capture_is_restricted_to_x11_owner_pid(monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    covered = _Transport(pid=7001)
    owner = _Transport(pid=7804)
    driver = JavaAgentDriver()
    driver.refresh_transports = lambda: (covered, owner)

    capture = driver.capture_at_point(945, 331, process_id=7804)

    assert capture.name == "Display"
    assert covered.calls == []
    assert owner.calls == [("hit_test", {"x": 945, "y": 331})]


def test_java_agent_does_not_claim_legacy_javafx_endpoint(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URLS", raising=False)
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_TOKENS", raising=False)
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_TOKEN", raising=False)
    monkeypatch.setenv("AUTOMATION_HARNESS_JAVAFX_AGENT_URL", "http://127.0.0.1:9417")
    monkeypatch.setenv("AUTOMATION_HARNESS_JAVAFX_AGENT_TOKEN", "legacy")
    monkeypatch.setenv("AUTOMATION_HARNESS_JAVA_AGENT_DISCOVERY_DIR", str(tmp_path))

    assert configured_java_agent_transports() == ()


def test_java_agent_is_discovered_without_manual_url_or_token(monkeypatch, tmp_path):
    discovery = tmp_path / "java-7145.json"
    discovery.write_text(
        '{"protocol":"automation-harness-java-agent/1","pid":7145,'
        '"host":"127.0.0.1","port":44123,"token":"generated"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        java_agent.HttpJavaFxBridgeTransport,
        "request",
        lambda self, operation, payload: {"status": "ok"},
    )

    transports = discover_java_agent_transports(tmp_path)

    assert [(item.endpoint, item.token, item.pid) for item in transports] == [
        ("http://127.0.0.1:44123", "generated", 7145)
    ]


def test_recording_uses_mixed_and_legacy_endpoints_without_duplicates(monkeypatch):
    monkeypatch.setenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", "http://127.0.0.1:9418")
    monkeypatch.setenv("AUTOMATION_HARNESS_JAVA_AGENT_TOKEN", "mixed")
    monkeypatch.setenv(
        "AUTOMATION_HARNESS_JAVAFX_AGENT_URLS",
        "http://127.0.0.1:9417,http://127.0.0.1:9418",
    )
    monkeypatch.setenv("AUTOMATION_HARNESS_JAVAFX_AGENT_TOKENS", "legacy,mixed")

    transports = configured_java_recording_transports()

    assert [(item.endpoint, item.token) for item in transports] == [
        ("http://127.0.0.1:9418", "mixed"),
        ("http://127.0.0.1:9417", "legacy"),
    ]


def test_java_agent_forwards_solipsys_rendered_identity(monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    driver = JavaAgentDriver()
    transport = _Transport(pid=5104)
    driver.refresh_transports = lambda: (transport,)

    driver.inspect(identification={
        "mandatory": {
            "rendered_class": "com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
            "track_class": "com.solipsys.msct.track.report.MSCTTrackReport",
            "track_identity_key": "getTrackId",
            "track_identity_value": "T-1234",
        },
        "assistive": {
            "native_class": "com.solipsys.view.AWTViewCanvas",
            "accessible_id": "panel0",
            "window": "MSCT",
        },
    })

    assert transport.calls == [("resolve", {
        "accessible_id": "panel0",
        "native_class": "com.solipsys.view.AWTViewCanvas",
        "window": "MSCT",
        "rendered_class": "com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
        "track_class": "com.solipsys.msct.track.report.MSCTTrackReport",
        "track_identity_key": "getTrackId",
        "track_identity_value": "T-1234",
    })]


def test_java_agent_select_menu_path_sends_stable_segment_identity(monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    driver = JavaAgentDriver()
    transport = _Transport(pid=5104)
    driver.refresh_transports = lambda: (transport,)

    result = driver.select_menu_path(
        [
            {"kind": "menu", "criteria": {"id": "fileMenu", "text": "File"}, "ordinal": 0},
            {"kind": "menu_item", "criteria": {"text": "Export"}, "ordinal": 3},
        ],
        identification={
            "mandatory": {"accessible_id": "mainMenu"},
            "assistive": {"window": "MSCT"},
        },
    )

    assert result["action"] == "select_menu_item"
    operation, payload = transport.calls[0]
    assert operation == "select_menu_path"
    assert payload == {
        "accessible_id": "mainMenu",
        "window": "MSCT",
        "menu_count": 2,
        "menu_0_id": "fileMenu",
        "menu_0_text": "File",
        "menu_0_ordinal": 0,
        "menu_1_text": "Export",
        "menu_1_ordinal": 3,
    }


def test_java_agent_exposes_distinct_focus_and_window_operations(monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    driver = JavaAgentDriver()
    transport = _Transport(pid=5104)
    driver.refresh_transports = lambda: (transport,)
    identity = {"mandatory": {"accessible_id": "display"}}

    driver.focus(identification=identity)
    driver.activate_window(identification=identity)

    assert [call[0] for call in transport.calls] == ["focus", "activate_window"]



class _ValueTransport(_Transport):
    def request(self, operation, payload):
        self.calls.append((operation, payload))
        if operation == "get_text":
            return {"text": "alpha\nbeta"}
        if operation == "get_value":
            return {"value": 42}
        return {
            "semantic_node": {
                "framework": "swing",
                "class": "javax.swing.JTextField",
                "native_class": "javax.swing.JTextField",
                "name": "Username",
                "accessible_id": "username",
                "window": "MSCT",
                "role": "text field",
                "bounds": [10, 20, 200, 24],
                "properties": {},
            }
        }


def test_java_agent_text_operations_use_native_protocol(monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    driver = JavaAgentDriver()
    transport = _ValueTransport(pid=5104)
    driver.refresh_transports = lambda: (transport,)
    identity = {"mandatory": {"accessible_id": "username"}}

    assert driver.get_text(identification=identity) == "alpha\nbeta"
    result = driver.set_text("first\nsecond", identification=identity)

    assert result["action"] == "set_text"
    assert transport.calls == [
        ("get_text", {"accessible_id": "username"}),
        ("set_text", {
            "accessible_id": "username",
            "value": "first\nsecond",
        }),
    ]


def test_java_agent_indexed_selection_uses_native_protocol(monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    driver = JavaAgentDriver()
    transport = _ValueTransport(pid=5104)
    driver.refresh_transports = lambda: (transport,)

    result = driver.select_child(
        3,
        identification={"mandatory": {"accessible_id": "cameraCombo"}},
    )

    assert result["selected_index"] == 3
    assert transport.calls == [
        ("select_child", {
            "accessible_id": "cameraCombo",
            "index": 3,
        })
    ]


def test_java_agent_numeric_value_operations_use_native_protocol(monkeypatch):
    monkeypatch.delenv("AUTOMATION_HARNESS_JAVA_AGENT_URL", raising=False)
    driver = JavaAgentDriver()
    transport = _ValueTransport(pid=5104)
    driver.refresh_transports = lambda: (transport,)
    identity = {"mandatory": {"accessible_id": "zoom"}}

    assert driver.get_value(identification=identity) == 42.0
    result = driver.set_value(17.5, identification=identity)

    assert result["action"] == "set_value"
    assert result["value"] == 17.5
    assert transport.calls == [
        ("get_value", {"accessible_id": "zoom"}),
        ("set_value", {"accessible_id": "zoom", "value": 17.5}),
    ]
