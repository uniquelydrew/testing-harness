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
    driver.transports = (transport,)

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
    driver.transports = (covered, owner)

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
