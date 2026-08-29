from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import uuid
import socket
from pathlib import Path

from automation_harness.backends.base import ExecutionBackend
from automation_harness.models.run import BackendHealth
from automation_harness.reference.protocol import ReferenceClient


class ReferenceBackend(ExecutionBackend):
    name = "reference"

    def __init__(self, *, gui: bool = True, display_mode: str = "virtual") -> None:
        if display_mode not in {"virtual", "native", "auto"}:
            raise ValueError(f"unsupported reference display mode: {display_mode}")
        self.gui = gui
        self.display_mode = display_mode
        self._process: subprocess.Popen[str] | None = None
        self._xvfb_process: subprocess.Popen[str] | None = None
        self._socket_path: Path | None = None
        self._endpoint: str | None = None
        self._stdout_handle = None
        self._stderr_handle = None
        self._xvfb_stdout_handle = None
        self._xvfb_stderr_handle = None
        self._display: str | None = None

    @property
    def allowed_step_risks(self) -> frozenset[str]:
        return frozenset({"read_only", "synthetic_control", "application_control"})

    @property
    def capabilities(self) -> set[str]:
        capabilities = {"reference", "local-only", "synthetic-events", "tracking", "mosaic", "threat-state", "triangulation"}
        if self.gui:
            capabilities.update({"gui", "components", "screen-capture", "synthetic-video"})
        return capabilities

    def preflight_issues(self) -> list[str]:
        issues: list[str] = []
        if not self.gui:
            return issues
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk  # noqa: F401
        except (ImportError, ValueError):
            issues.append("GUI reference mode requires PyGObject/GTK 3 support")
        if self.display_mode == "virtual" and shutil.which("Xvfb") is None:
            issues.append("virtual GUI reference mode requires Xvfb on PATH")
        if self.display_mode == "native" and not os.environ.get("DISPLAY"):
            issues.append("native GUI reference mode requires DISPLAY to be set")
        if self.display_mode == "auto" and not os.environ.get("DISPLAY") and shutil.which("Xvfb") is None:
            issues.append("auto GUI reference mode requires either DISPLAY or Xvfb")
        try:
            from PIL import ImageGrab  # noqa: F401
        except ImportError:
            issues.append("GUI reference mode requires Pillow for framebuffer evidence")
        return issues

    def start(self, *, run_dir: Path) -> dict[str, str]:
        if self._process is not None:
            raise RuntimeError("reference backend already started")
        use_tcp = os.name == "nt"
        socket_path = Path("/tmp") / f"automation-run-{os.getpid()}-{uuid.uuid4().hex[:12]}.sock"
        endpoint = f"tcp://127.0.0.1:{_choose_tcp_port()}" if use_tcp else str(socket_path)
        self._socket_path = Path(endpoint) if not use_tcp else None
        self._endpoint = endpoint
        self._stdout_handle = (run_dir / "logs" / "reference.stdout.log").open("w", encoding="utf-8")
        self._stderr_handle = (run_dir / "logs" / "reference.stderr.log").open("w", encoding="utf-8")
        env = os.environ.copy()
        package_parent = str(Path(__file__).resolve().parents[2])
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [package_parent, env.get("PYTHONPATH", "")]))
        if self.gui:
            self._display = self._prepare_display(run_dir, env)
            env["DISPLAY"] = self._display
        command = [sys.executable, "-m", "automation_harness.reference.app"]
        if use_tcp:
            command.extend(["--tcp-port", endpoint.rsplit(":", 1)[1]])
        else:
            command.extend(["--socket", str(socket_path)])
        command.append("--gui" if self.gui else "--headless")
        self._process = subprocess.Popen(command, stdout=self._stdout_handle, stderr=self._stderr_handle, text=True, env=env)
        deadline = time.monotonic() + 8.0
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(self._startup_failure(command, env))
            if use_tcp or socket_path.exists():
                try:
                    health = ReferenceClient(endpoint).request("health")
                    gui_ready = bool(health.get("ui_ready"))
                    if health.get("status") == "ok" and (not self.gui or gui_ready):
                        result = {
                            "AUTOMATION_HARNESS_BACKEND": self.name,
                            "AUTOMATION_HARNESS_SOCKET": endpoint,
                            "AUTOMATION_HARNESS_REFERENCE_MODE": "gui" if self.gui else "headless",
                        }
                        if self._display is not None:
                            result["DISPLAY"] = self._display
                        return result
                except Exception as exc:
                    last_error = exc
            time.sleep(0.05)
        raise RuntimeError(f"reference backend did not become healthy: {last_error}; {self._startup_failure(command, env)}")

    def _startup_failure(self, command: list[str], env: dict[str, str]) -> str:
        """Return evidence-safe startup diagnostics for runner error evidence."""
        exit_code = self._process.returncode if self._process is not None else None
        def tail(handle) -> str:
            if handle is None:
                return ""
            handle.flush()
            try:
                return Path(handle.name).read_text(encoding="utf-8")[-4000:]
            except OSError:
                return "<unavailable>"
        environment = {key: env.get(key) for key in ("DISPLAY", "PYTHONPATH", "PATH") if env.get(key)}
        return (
            f"reference backend startup diagnostics: exit_code={exit_code}; command={command!r}; "
            f"environment={environment!r}; stdout={tail(self._stdout_handle)!r}; stderr={tail(self._stderr_handle)!r}"
        )

    def _prepare_display(self, run_dir: Path, env: dict[str, str]) -> str:
        current = env.get("DISPLAY")
        if self.display_mode == "native":
            if not current:
                raise RuntimeError("reference display mode 'native' requires DISPLAY")
            return current
        if self.display_mode == "auto" and current:
            return current
        return self._start_xvfb(run_dir)

    def _start_xvfb(self, run_dir: Path) -> str:
        executable = shutil.which("Xvfb")
        if not executable:
            raise RuntimeError("Xvfb is required for virtual GUI reference runs but was not found")
        display_number = _choose_display_number()
        display = f":{display_number}"
        self._xvfb_stdout_handle = (run_dir / "logs" / "xvfb.stdout.log").open("w", encoding="utf-8")
        self._xvfb_stderr_handle = (run_dir / "logs" / "xvfb.stderr.log").open("w", encoding="utf-8")
        self._xvfb_process = subprocess.Popen([executable, display, "-screen", "0", "1024x768x24", "-nolisten", "tcp", "-ac"], stdout=self._xvfb_stdout_handle, stderr=self._xvfb_stderr_handle, text=True)
        socket_path = Path(f"/tmp/.X11-unix/X{display_number}")
        deadline = time.monotonic() + 4.0
        while time.monotonic() < deadline:
            if self._xvfb_process.poll() is not None:
                raise RuntimeError(f"Xvfb exited during startup with code {self._xvfb_process.returncode}")
            if socket_path.exists():
                return display
            time.sleep(0.05)
        raise RuntimeError(f"Xvfb did not create display socket for {display}")

    def health_check(self) -> BackendHealth:
        if self._process is None or self._endpoint is None or self._process.poll() is not None:
            return BackendHealth(False, self.name, {"reason": "not running"})
        try:
            result = ReferenceClient(self._endpoint).request("health")
            healthy = result.get("status") == "ok" and (not self.gui or bool(result.get("ui_ready")))
            details = dict(result); details["mode"] = "gui" if self.gui else "headless"; details["display"] = self._display
            return BackendHealth(healthy, self.name, details)
        except Exception as exc:
            return BackendHealth(False, self.name, {"error": str(exc)})

    def stop(self) -> None:
        self._stop_process(self._process); self._process = None
        self._stop_process(self._xvfb_process); self._xvfb_process = None
        for handle_name in ("_stdout_handle", "_stderr_handle", "_xvfb_stdout_handle", "_xvfb_stderr_handle"):
            handle = getattr(self, handle_name)
            if handle is not None:
                handle.close(); setattr(self, handle_name, None)
        if self._socket_path is not None:
            try:
                self._socket_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._socket_path = None
        self._endpoint = None
        self._display = None

    @staticmethod
    def _stop_process(process: subprocess.Popen[str] | None) -> None:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=3)


def _choose_display_number() -> int:
    for number in range(90, 150):
        if not Path(f"/tmp/.X11-unix/X{number}").exists():
            return number
    raise RuntimeError("no free X display number found in range 90..149")


def _choose_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])
