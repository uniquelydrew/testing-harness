"""Locations for generated, machine-local Automation Harness artifacts."""

from __future__ import annotations

import os
from pathlib import Path


def runtime_root() -> Path:
    """Return the external root for logs, run evidence, and other runtime data."""
    configured = os.environ.get("AUTOMATION_HARNESS_RUNTIME_DIR")
    if configured:
        root = Path(configured).expanduser()
    elif os.environ.get("XDG_STATE_HOME"):
        root = Path(os.environ["XDG_STATE_HOME"]).expanduser() / "automation-harness"
    elif os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]).expanduser() / "Automation Harness"
    else:
        root = Path.home() / ".local" / "state" / "automation-harness"
    return root.resolve()


def runtime_path(*parts: str) -> Path:
    """Build a path below :func:`runtime_root` and reject a checkout override."""
    path = runtime_root().joinpath(*parts)
    ensure_external_runtime_path(path)
    return path


def ensure_external_runtime_path(path: Path) -> Path:
    """Reject generated output below a Git checkout.

    This deliberately applies to explicit user preferences as well as defaults:
    generated evidence must not dirty a source repository.
    """
    resolved = Path(path).expanduser().resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / ".git").exists():
            raise ValueError(
                "Runtime artifacts must be written outside a Git checkout: %s" % resolved
            )
    return resolved
