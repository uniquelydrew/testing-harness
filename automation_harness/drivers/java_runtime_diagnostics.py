"""Runtime metadata emitted by mixed Java agents.

Each successfully loaded mixed agent writes a process-scoped sidecar next to its
endpoint discovery file.  Keeping this separate from endpoint health means the
harness can preserve the exact JVM facts used to decide agent compatibility.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def discovery_directory(path=None) -> Path:
    if path is not None:
        return Path(path)
    return Path(os.environ.get(
        "AUTOMATION_HARNESS_JAVA_AGENT_DISCOVERY_DIR",
        "/tmp/automation-harness-java-agent",
    ))


def collect_java_runtime_diagnostics(path=None) -> tuple[dict[str, Any], ...]:
    """Return valid JVM runtime snapshots, ordered by PID.

    Stale sidecars are ignored unless their corresponding mixed-agent endpoint
    discovery file still exists.  This keeps diagnostics aligned with JVMs the
    harness can currently discover rather than historical processes.
    """
    directory = discovery_directory(path)
    if not directory.is_dir():
        return ()
    snapshots = []
    for runtime_path in directory.glob("java-*-runtime.json"):
        try:
            raw = json.loads(runtime_path.read_text(encoding="utf-8"))
            pid = int(raw["pid"])
            if pid <= 0 or not (directory / ("java-%s.json" % pid)).is_file():
                continue
            snapshot = dict(raw)
            snapshot["pid"] = pid
            snapshot["runtime_diagnostic_file"] = str(runtime_path)
            snapshots.append(snapshot)
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
    return tuple(sorted(snapshots, key=lambda item: item["pid"]))


def java_runtime_for_pid(pid: int, path=None) -> dict[str, Any] | None:
    wanted = int(pid)
    for snapshot in collect_java_runtime_diagnostics(path):
        if snapshot["pid"] == wanted:
            return snapshot
    return None
