from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from automation_harness.backends.base import ExecutionBackend
from automation_harness.models.run import BackendHealth


class JavaDesktopBackend(ExecutionBackend):
    """Managed black-box Swing/JavaFX target for Windows and Ubuntu/X11."""

    name = "java-desktop"

    def __init__(self, target: Mapping[str, Any], *, display_mode: str = "virtual") -> None:
        if display_mode not in {"virtual", "native", "auto"}:
            raise ValueError(f"unsupported Java desktop display mode: {display_mode}")
        self.target = dict(target)
        self.command = list(self.target["command"])
        self.working_directory = self.target.get("working_directory")
        self.expected_application = self.target.get("expected_application")
        self.extra_environment = dict(self.target.get("environment", {}))
        self.startup_timeout = float(self.target.get("startup_timeout", 12.0))
        self.display_mode = display_mode
        self._process: subprocess.Popen[str] | None = None
        self._xvfb: subprocess.Popen[str] | None = None
        self._display: str | None = None
        self._handles: list[Any] = []

    @property
    def capabilities(self) -> set[str]:
        return {"local-only", "gui", "components", "java-accessibility", "screen-capture", "vision"}

    @property
    def allowed_step_risks(self) -> frozenset[str]:
        return frozenset({"read_only", "application_control"})

    def preflight_issues(self) -> list[str]:
        system = platform.system()
        if system not in {"Windows", "Linux"}:
            return [f"Java desktop backend is supported on Windows or Linux, not {system}"]
        if self.working_directory and not Path(self.working_directory).is_dir():
            return [f"Java desktop target working_directory does not exist: {self.working_directory}"]
        executable = self.command[0]
        if not Path(executable).is_file() and shutil.which(executable) is None:
            return [f"Java desktop target command was not found: {executable!r}"]
        if system == "Windows":
            try:
                from automation_harness.drivers.java_accessibility import JavaAccessBridgeDriver

                JavaAccessBridgeDriver.require_available()
            except Exception as exc:
                return [f"Windows Java Access Bridge is unavailable: {exc}"]
            return []
        issues: list[str] = []
        for package in ("libatk-wrapper-java", "libatk-wrapper-java-jni"):
            completed = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}", package], text=True, capture_output=True)
            if completed.returncode != 0 or completed.stdout.strip() != "installed":
                issues.append(f"Linux Java accessibility requires package {package}")
        try:
            import pyatspi  # type: ignore # noqa: F401
        except ImportError:
            issues.append("Linux Java accessibility requires the system pyatspi binding")
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            issues.append("Linux Java accessibility requires a D-Bus session")
        if self.display_mode == "virtual" and shutil.which("Xvfb") is None:
            issues.append("virtual Java desktop runs require Xvfb on PATH")
        if self.display_mode == "native" and not os.environ.get("DISPLAY"):
            issues.append("native Java desktop runs require DISPLAY")
        return issues

    def start(self, *, run_dir: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self.extra_environment)
        if platform.system() == "Linux":
            self._display = self._prepare_display(run_dir)
            env["DISPLAY"] = self._display
            _enable_java_atk_wrapper(env, self.command)
        stdout = (run_dir / "logs" / "java-desktop.stdout.log").open("w", encoding="utf-8")
        stderr = (run_dir / "logs" / "java-desktop.stderr.log").open("w", encoding="utf-8")
        self._handles.extend((stdout, stderr))
        self._process = subprocess.Popen(
            self.command,
            cwd=self.working_directory,
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise RuntimeError(f"Java desktop process exited during startup with code {self._process.returncode}")
            if self._target_ready():
                result = {"AUTOMATION_HARNESS_BACKEND": self.name, "AUTOMATION_HARNESS_JAVA_COMMAND": self.command[0]}
                if self._display:
                    result["DISPLAY"] = self._display
                return result
            time.sleep(0.1)
        raise RuntimeError(f"Java desktop target did not become accessible within {self.startup_timeout:g}s")

    def health_check(self) -> BackendHealth:
        alive = self._process is not None and self._process.poll() is None
        return BackendHealth(alive and self._target_ready(), self.name, {
            "process_alive": alive,
            "command": self.command,
            "expected_application": self.expected_application,
            "display": self._display,
            "platform": platform.system(),
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

    def _target_ready(self) -> bool:
        from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver

        return JavaAccessibilityDriver.application_present(self.expected_application)

    def _prepare_display(self, run_dir: Path) -> str:
        current = os.environ.get("DISPLAY")
        if self.display_mode == "native":
            if not current:
                raise RuntimeError("native Java desktop runs require DISPLAY")
            return current
        if self.display_mode == "auto" and current:
            return current
        for number in range(200, 250):
            if Path(f"/tmp/.X11-unix/X{number}").exists():
                continue
            display = f":{number}"
            stdout = (run_dir / "logs" / "xvfb.stdout.log").open("w", encoding="utf-8")
            stderr = (run_dir / "logs" / "xvfb.stderr.log").open("w", encoding="utf-8")
            self._handles.extend((stdout, stderr))
            self._xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x900x24", "-nolisten", "tcp", "-ac"], stdout=stdout, stderr=stderr, text=True)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                if self._xvfb.poll() is not None:
                    break
                if Path(f"/tmp/.X11-unix/X{number}").exists():
                    return display
                time.sleep(0.05)
            self.stop()
        raise RuntimeError("no Xvfb display was available in range :200-:249")


def _enable_java_atk_wrapper(env: dict[str, str], command: list[str]) -> None:
    """Enable the Java ATK wrapper without requiring an application test hook."""
    property_arg = "-Djavax.accessibility.assistive_technologies=org.GNOME.Accessibility.AtkWrapper"
    if any(arg.startswith("-Djavax.accessibility.assistive_technologies=") for arg in command):
        return
    if Path(command[0]).name.casefold().startswith("java"):
        command.insert(1, property_arg)
    else:
        options = env.get("JAVA_TOOL_OPTIONS", "").strip()
        env["JAVA_TOOL_OPTIONS"] = " ".join(filter(None, [options, property_arg]))
