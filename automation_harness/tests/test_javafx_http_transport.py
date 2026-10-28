import json
from unittest.mock import patch

import pytest

from automation_harness.drivers.javafx_bridge import HttpJavaFxBridgeTransport


def test_transport_rejects_non_loopback_endpoint():
    with pytest.raises(ValueError, match="loopback"):
        HttpJavaFxBridgeTransport("https://example.com", "secret").request("record_start", {})


def test_transport_posts_authenticated_operation():
    class _Response:
        def read(self): return json.dumps({"ok": True, "result": {"observations": []}}).encode()
        def __enter__(self): return self
        def __exit__(self, *_args): return None
    with patch("automation_harness.drivers.javafx_bridge.urlopen", return_value=_Response()) as open_url:
        result = HttpJavaFxBridgeTransport("http://127.0.0.1:9418", "token").request("record_read", {"timeout": 1})
    request = open_url.call_args[0][0]
    assert request.full_url == "http://127.0.0.1:9418/record_read"
    assert request.get_header("X-automation-harness-token") == "token"
    assert result == {"observations": []}


def test_http_transport_serializes_frozen_nested_capture_metadata():
    from types import MappingProxyType
    from automation_harness.drivers.javafx_bridge import JavaFxBridgeProtocolError

    class _Response:
        def read(self):
            return b'{"ok":true,"result":{"observations":[]}}'
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False

    nested = MappingProxyType({
        "identification": MappingProxyType({
            "mandatory": MappingProxyType({"id": "cameraSelectorMenuItem"}),
            "lineage": (MappingProxyType({"class": "javafx.scene.control.Menu"}),),
        }),
    })
    with patch("automation_harness.drivers.javafx_bridge.urlopen", return_value=_Response()) as open_url:
        assert HttpJavaFxBridgeTransport(
            "http://127.0.0.1:9418", "test-token",
        ).request("find", nested) == {"observations": []}
    submitted = json.loads(open_url.call_args[0][0].data.decode("utf-8"))
    assert submitted == {"identification": {
        "mandatory": {"id": "cameraSelectorMenuItem"},
        "lineage": [{"class": "javafx.scene.control.Menu"}],
    }}

    with patch("automation_harness.drivers.javafx_bridge.urlopen") as open_url:
        with pytest.raises(JavaFxBridgeProtocolError, match=r"\$\.identification\.mandatory\.id"):
            HttpJavaFxBridgeTransport(
                "http://127.0.0.1:9418", "test-token",
            ).request("find", {"identification": {"mandatory": {"id": object()}}})
        open_url.assert_not_called()
