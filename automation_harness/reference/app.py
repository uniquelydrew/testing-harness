from __future__ import annotations

import argparse
import json
import signal
import socketserver
import threading
from pathlib import Path
from typing import Any

from automation_harness.reference.state import ReferenceState


class ReferenceHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline()
        if not raw:
            return
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            action = request.get("action")
            if not isinstance(action, str):
                raise ValueError("request.action must be a string")
            result = self.server.state.handle(action, request)  # type: ignore[attr-defined]
            response: dict[str, Any] = {"ok": True, "result": result}
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.wfile.write((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))


if hasattr(socketserver, "ThreadingUnixStreamServer"):
    class ReferenceServer(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
        allow_reuse_address = True

        def __init__(self, socket_path: str, state: ReferenceState) -> None:
            self.state = state
            super().__init__(socket_path, ReferenceHandler)
else:  # pragma: no cover - the Unix endpoint is never selected on Windows.
    ReferenceServer = None  # type: ignore[assignment,misc]


class ReferenceTcpServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], state: ReferenceState) -> None:
        self.state = state
        super().__init__(address, ReferenceHandler)


def serve(socket_path: Path | None, *, gui: bool, tcp_port: int | None = None) -> None:
    if (socket_path is None) == (tcp_port is None):
        raise ValueError("exactly one reference endpoint must be selected")
    if socket_path is not None:
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        if socket_path.exists():
            socket_path.unlink()
    state = ReferenceState()
    if tcp_port is not None:
        server = ReferenceTcpServer(("127.0.0.1", tcp_port), state)
    else:
        if ReferenceServer is None:
            raise RuntimeError("Unix reference sockets are unavailable on this platform")
        server = ReferenceServer(str(socket_path), state)
    server_thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    server_thread.start()

    gui_target = None
    stop_event = threading.Event()

    def shutdown(_signum: int, _frame: object) -> None:
        if stop_event.is_set():
            return
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()
        if gui_target is not None:
            gui_target.close()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        if gui:
            from automation_harness.reference.gui import ReferenceGui

            gui_target = ReferenceGui(state)
            gui_target.run()
        else:
            while not stop_event.wait(0.1):
                if not server_thread.is_alive():
                    break
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
        if socket_path is not None and socket_path.exists():
            socket_path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the isolated automation reference target")
    endpoint = parser.add_mutually_exclusive_group(required=True)
    endpoint.add_argument("--socket", type=Path)
    endpoint.add_argument("--tcp-port", type=int)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--gui", action="store_true", help="run the synthetic desktop GUI")
    mode.add_argument("--headless", action="store_true", help="run service-only reference state")
    args = parser.parse_args()
    serve(args.socket.resolve() if args.socket else None, gui=bool(args.gui and not args.headless), tcp_port=args.tcp_port)


if __name__ == "__main__":
    main()
