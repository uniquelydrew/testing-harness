from automation_harness.drivers import java_agent


def test_java_agent_driver_refreshes_discovery_after_construction(monkeypatch):
    discovered = []

    def configured():
        return tuple(discovered)

    monkeypatch.setattr(java_agent, "configured_java_agent_transports", configured)
    driver = java_agent.JavaAgentDriver()

    assert driver.available is False

    endpoint = object()
    discovered.append(endpoint)

    assert driver.available is True
    assert driver.transports == (endpoint,)


def test_capture_uses_newly_discovered_transport(monkeypatch):
    class Transport:
        pid = 5887
        endpoint = "http://127.0.0.1:12345"
        token = "token"

        def request(self, operation, payload):
            assert operation == "hit_test"
            assert payload == {"x": 100, "y": 200}
            return {
                "semantic_node": {
                    "name": "MSCT Surface",
                    "role": "panel",
                    "description": None,
                    "accessible_id": None,
                    "application": "MSCT",
                    "hierarchy": ["MSCT", "MSCT Surface"],
                    "actions": [],
                    "bounds": [0, 0, 800, 600],
                    "state": {"present": True, "visible": True},
                    "backend_properties": {"bridge_pid": 5887},
                    "framework": "java_agent",
                    "native_class": "example.RenderSurface",
                }
            }

    discovered = []
    monkeypatch.setattr(
        java_agent,
        "configured_java_agent_transports",
        lambda: tuple(discovered),
    )
    driver = java_agent.JavaAgentDriver()
    discovered.append(Transport())

    captured = driver.capture_at_point(100, 200, process_id=5887)

    assert captured.name == "MSCT Surface"
    assert captured.backend_properties["bridge_pid"] == 5887
