"""Execution backend for an already-running desktop environment."""
from __future__ import annotations

import os
import platform
from pathlib import Path

from automation_harness.backends.base import ExecutionBackend
from automation_harness.drivers.atspi_driver import AtspiDriver
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.models.run import BackendHealth


class LiveDesktopBackend(ExecutionBackend):
    """Execute against the current desktop without owning application lifecycle.

    Application/window lineage belongs to each object locator. The backend is
    therefore intentionally targetless and never injects one application name
    into every component resolution.
    """

    name = "attached-desktop"

    @property
    def capabilities(self) -> set[str]:
        capabilities = {"local-only", "gui", "components", "java-accessibility"}
        try:
            from PIL import ImageGrab  # noqa: F401
        except ImportError:
            pass
        else:
            capabilities.update({"screen-capture", "vision"})
        return capabilities

    @property
    def allowed_step_risks(self) -> frozenset[str]:
        return frozenset({"read_only", "application_control"})

    def preflight_issues(self) -> list[str]:
        issues: list[str] = []
        if platform.system() not in {"Linux", "Windows"}:
            issues.append("live desktop execution is supported only on Linux or Windows")
        if platform.system() == "Linux":
            if not os.environ.get("DISPLAY"):
                issues.append("live desktop execution requires DISPLAY")
            if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
                issues.append("live desktop execution requires a D-Bus accessibility session")
        if not (AtspiDriver().available or JavaAccessibilityDriver().available):
            issues.append("no supported desktop accessibility driver is available")
        return issues

    def start(self, *, run_dir: Path) -> dict[str, str]:
        issues = self.preflight_issues()
        if issues:
            raise RuntimeError("; ".join(issues))
        environment = {"AUTOMATION_HARNESS_BACKEND": self.name}
        if os.environ.get("DISPLAY"):
            environment["DISPLAY"] = os.environ["DISPLAY"]
        if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
            environment["DBUS_SESSION_BUS_ADDRESS"] = os.environ["DBUS_SESSION_BUS_ADDRESS"]
        return environment

    def health_check(self) -> BackendHealth:
        issues = self.preflight_issues()
        return BackendHealth(
            not issues,
            self.name,
            {
                "attached": True,
                "target_application": None,
                "display": os.environ.get("DISPLAY"),
                "preflight_issues": issues,
            },
        )

    def stop(self) -> None:
        # The environment and every application in it belong to the user or
        # external startup script, never to the authoring console.
        return None
