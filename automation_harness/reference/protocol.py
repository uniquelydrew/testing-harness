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
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self.timeout)
            sock.connect(self.socket_path)
            sock.sendall(message.encode("utf-8"))
            response = self._readline(sock)
        data = json.loads(response)
        if not data.get("ok", False):
            raise ReferenceProtocolError(data.get("error", "reference backend request failed"))
        return data.get("result")

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
