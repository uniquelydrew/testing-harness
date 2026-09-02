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
    request = open_url.call_args.args[0]
    assert request.full_url == "http://127.0.0.1:9418/record_read"
    assert request.get_header("X-automation-harness-token") == "token"
    assert result == {"observations": []}
