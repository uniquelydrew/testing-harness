from automation_harness.drivers.java_agent import JavaAgentDriver


class _Transport:
    def __init__(self):
        self.calls = []

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
