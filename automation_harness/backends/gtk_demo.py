from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

from automation_harness.backends.base import ExecutionBackend
from automation_harness.models.run import BackendHealth


class GtkDemoBackend(ExecutionBackend):
    """Managed GTK 4.14 demo target for real AT-SPI qualification."""

    name = "gtk-demo"

    def __init__(self, *, example: str, executable: str | None = None, display_mode: str = "virtual") -> None:
        if not example:
            raise ValueError("GTK Demo example is required")
        if display_mode not in {"virtual", "native", "auto"}:
            raise ValueError(f"unsupported GTK Demo display mode: {display_mode}")
        self.example = example
        self.executable = executable or os.environ.get("AUTOMATION_HARNESS_GTK_DEMO", "gtk4-demo")
        self.display_mode = display_mode
        self._resolved_executable: str | None = None
        self._version: str | None = None
        self._process: subprocess.Popen[str] | None = None
        self._xvfb: subprocess.Popen[str] | None = None
        self._display: str | None = None
        self._handles = []

    @property
    def capabilities(self) -> set[str]:
        return {"local-only", "gui", "components", "atspi", "text-input", "selection", "value-control"}

    @property
    def allowed_step_risks(self) -> frozenset[str]:
        return frozenset({"read_only", "application_control"})

    def preflight_issues(self) -> list[str]:
        issues: list[str] = []
        if platform.system() != "Linux":
            issues.append("GTK Demo backend is supported only on Linux")
        executable = shutil.which(self.executable) if not Path(self.executable).is_file() else self.executable
        if not executable:
            issues.append(f"GTK Demo executable not found: {self.executable!r}")
            return issues
        self._resolved_executable = executable
        try:
            version = _read_version(executable)
            self._version = version
            if not re.fullmatch(r"4\.14\.\d+", version):
                issues.append(f"GTK Demo baseline requires GTK 4.14.x; detected {version}")
        except Exception as exc:
            issues.append(f"unable to determine GTK Demo version: {exc}")
        try:
            import pyatspi  # type: ignore # noqa: F401
        except ImportError:
            issues.append("GTK Demo backend requires the system pyatspi binding")
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            issues.append("GTK Demo backend requires an AT-SPI D-Bus session (run under dbus-run-session or a desktop session)")
        if self.display_mode == "virtual" and shutil.which("Xvfb") is None:
            issues.append("virtual GTK Demo runs require Xvfb on PATH")
        if self.display_mode == "native" and not os.environ.get("DISPLAY"):
            issues.append("native GTK Demo runs require DISPLAY")
        return issues

    def start(self, *, run_dir: Path) -> dict[str, str]:
        executable = self._resolved_executable or shutil.which(self.executable) or self.executable
        self._display = self._prepare_display(run_dir)
        env = os.environ.copy()
        env["DISPLAY"] = self._display
        stdout = (run_dir / "logs" / "gtk-demo.stdout.log").open("w", encoding="utf-8")
        stderr = (run_dir / "logs" / "gtk-demo.stderr.log").open("w", encoding="utf-8")
        self._handles.extend((stdout, stderr))
        self._process = subprocess.Popen([executable, "--run", self.example], stdout=stdout, stderr=stderr, text=True, env=env)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"gtk4-demo exited during startup with code {self._process.returncode}")
            if _atspi_target_ready():
                return {
                    "AUTOMATION_HARNESS_BACKEND": self.name,
                    "AUTOMATION_HARNESS_GTK_DEMO_EXAMPLE": self.example,
                    "AUTOMATION_HARNESS_GTK_DEMO_VERSION": self._version or "unknown",
                    "DISPLAY": self._display,
                }
            time.sleep(0.1)
        raise RuntimeError("gtk4-demo did not appear on the AT-SPI desktop within 8 seconds")

    def health_check(self) -> BackendHealth:
        alive = self._process is not None and self._process.poll() is None
        ready = alive and _atspi_target_ready()
        return BackendHealth(bool(ready), self.name, {
            "example": self.example, "executable": self._resolved_executable or self.executable,
            "version": self._version, "display": self._display, "process_alive": alive,
        })

    def stop(self) -> None:
        for process in (self._process, self._xvfb):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        self._process = self._xvfb = None
        for handle in self._handles:
            handle.close()
        self._handles.clear()
        self._display = None

    def _prepare_display(self, run_dir: Path) -> str:
        display = os.environ.get("DISPLAY")
        if self.display_mode == "native":
            if not display:
                raise RuntimeError("native GTK Demo runs require DISPLAY")
            return display
        if self.display_mode == "auto" and display:
            return display
        for number in range(150, 200):
            candidate = f":{number}"
            if Path(f"/tmp/.X11-unix/X{number}").exists():
                continue
            stdout = (run_dir / "logs" / "xvfb.stdout.log").open("w", encoding="utf-8")
            stderr = (run_dir / "logs" / "xvfb.stderr.log").open("w", encoding="utf-8")
            self._handles.extend((stdout, stderr))
            self._xvfb = subprocess.Popen(["Xvfb", candidate, "-screen", "0", "1280x900x24", "-nolisten", "tcp", "-ac"], stdout=stdout, stderr=stderr, text=True)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if self._xvfb.poll() is not None:
                    break
                if Path(f"/tmp/.X11-unix/X{number}").exists():
                    return candidate
                time.sleep(0.05)
            self.stop()
        raise RuntimeError("no Xvfb display was available in range :150-:199")


def _read_version(executable: str) -> str:
    completed = subprocess.run([executable, "--version"], text=True, capture_output=True, check=True, timeout=5)
    match = re.search(r"(\d+\.\d+\.\d+)", completed.stdout + completed.stderr)
    if not match:
        raise ValueError("version number was not present in gtk4-demo --version output")
    return match.group(1)


def _atspi_target_ready() -> bool:
    """Confirm that GTK Demo, not merely an AT-SPI registry, is present."""
    try:
        import pyatspi  # type: ignore
        desktop = pyatspi.Registry.getDesktop(0)
        for index in range(int(desktop.childCount)):
            application = desktop.getChildAtIndex(index)
            name = str(getattr(application, "name", "")).casefold()
            if "gtk" in name and "demo" in name:
                return True
        return False
    except Exception:
        return False
