"""Execution backend for an application already running on the user's desktop."""
from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Any, Mapping

from automation_harness.backends.base import ExecutionBackend
from automation_harness.drivers.java_accessibility import JavaAccessibilityDriver
from automation_harness.models.run import BackendHealth


class AttachedDesktopBackend(ExecutionBackend):
    """Use accessibility to control a live process without owning its lifecycle."""

    name = "attached-desktop"

    def __init__(self, target: Mapping[str, Any]) -> None:
        self.target = dict(target)
        self.expected_application = self.target.get("expected_application")
        if not isinstance(self.expected_application, str) or not self.expected_application.strip():
            raise ValueError("attached-desktop target requires expected_application")
        self.expected_application = self.expected_application.strip()

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
            issues.append("attached desktop execution is supported only on Linux or Windows")
        if platform.system() == "Linux":
            if not os.environ.get("DISPLAY"):
                issues.append("attached desktop execution requires DISPLAY")
            if not os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
                issues.append("attached desktop execution requires a D-Bus accessibility session")
        if not JavaAccessibilityDriver().available:
            issues.append("no supported desktop accessibility driver is available")
        if not JavaAccessibilityDriver.application_present(self.expected_application):
            issues.append("application %r is not currently accessible" % self.expected_application)
        return issues

    def start(self, *, run_dir: Path) -> dict[str, str]:
        issues = self.preflight_issues()
        if issues:
            raise RuntimeError("; ".join(issues))
        result = {
            "AUTOMATION_HARNESS_BACKEND": self.name,
            "AUTOMATION_HARNESS_ATTACHED_APPLICATION": self.expected_application,
        }
        if os.environ.get("DISPLAY"):
            result["DISPLAY"] = os.environ["DISPLAY"]
        return result

    def health_check(self) -> BackendHealth:
        present = JavaAccessibilityDriver.application_present(self.expected_application)
        return BackendHealth(present, self.name, {
            "attached": True,
            "expected_application": self.expected_application,
            "application_present": present,
            "display": os.environ.get("DISPLAY"),
        })

    def stop(self) -> None:
        # An attached target belongs to the user, not the harness.
        return None
