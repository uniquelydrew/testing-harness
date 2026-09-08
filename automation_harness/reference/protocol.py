from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


class ReferenceProtocolError(RuntimeError):
    pass


class ReferenceClient:
    def __init__(self, socket_path: str | Path, *, timeout: float = 3.0) -> None:
        self.socket_path = str(socket_path)
        self.timeout = timeout

    def request(self, action: str, **payload: Any) -> Any:
        message = json.dumps({"action": action, **payload}, separators=(",", ":")) + "\n"
        family, address = self._endpoint()
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(address)
            sock.sendall(message.encode("utf-8"))
            response = self._readline(sock)
        data = json.loads(response)
        if not data.get("ok", False):
            raise ReferenceProtocolError(data.get("error", "reference backend request failed"))
        return data.get("result")

    def _endpoint(self) -> tuple[int, str | tuple[str, int]]:
        """Return a portable endpoint while retaining Unix-socket compatibility.

        Windows does not provide ``AF_UNIX`` in every supported Python/runtime
        combination.  The reference target therefore also accepts an explicit
        ``tcp://host:port`` endpoint for its isolated loopback control channel.
        """
        if self.socket_path.startswith("tcp://"):
            host_port = self.socket_path.removeprefix("tcp://")
            host, separator, port = host_port.rpartition(":")
            if not separator or not host or not port.isdecimal():
                raise ReferenceProtocolError(f"invalid TCP reference endpoint {self.socket_path!r}")
            return socket.AF_INET, (host, int(port))
        return socket.AF_UNIX, self.socket_path

    @staticmethod
    def _readline(sock: socket.socket) -> str:
        chunks: list[bytes] = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break
        raw = b"".join(chunks).split(b"\n", 1)[0]
        if not raw:
            raise ReferenceProtocolError("reference backend returned no response")
        return raw.decode("utf-8")
