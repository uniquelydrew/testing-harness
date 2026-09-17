"""Persistent, verbose recording diagnostics for field troubleshooting."""
from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import traceback
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


_SENSITIVE = ("TOKEN", "PASSWORD", "PASSWD", "SECRET", "CREDENTIAL", "API_KEY")


class RecordingDebugLog:
    def __init__(self, directory: Path, *, label: str = "recording") -> None:
        directory = Path(directory).expanduser().resolve()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.path = directory / ("%s-debug-%s-p%s.txt" % (label, stamp, os.getpid()))
        self._lock = threading.RLock()
        self._sequence = 0
        self.write("diagnostic_log_created", runtime=self.runtime_snapshot())

    def write(self, event: str, **payload: Any) -> None:
        with self._lock:
            self._sequence += 1
            record = {
                "sequence": self._sequence,
                "timestamp": datetime.now().astimezone().isoformat(),
                "monotonic": _safe_monotonic(),
                "thread": {"name": threading.current_thread().name, "ident": threading.get_ident()},
                "event": str(event),
                "payload": _serializable(payload),
            }
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write("\n===== %06d %s =====\n" % (self._sequence, event))
                stream.write(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False))
                stream.write("\n")

    def exception(self, event: str, error: BaseException, **payload: Any) -> None:
        self.write(
            event,
            error_type=type(error).__name__,
            error=str(error),
            traceback="".join(traceback.format_exception(type(error), error, error.__traceback__)),
            **payload
        )

    @staticmethod
    def runtime_snapshot() -> Mapping[str, Any]:
        environment = {}
        for key, value in sorted(os.environ.items()):
            if key.startswith(("AUTOMATION_HARNESS_", "JAVA_", "XDG_")) or key in {
                "DISPLAY", "WAYLAND_DISPLAY", "DESKTOP_SESSION", "GDK_BACKEND",
            }:
                environment[key] = "<redacted>" if any(item in key.upper() for item in _SENSITIVE) else _redact_text(value)
        return {
            "argv": list(sys.argv),
            "executable": sys.executable,
            "python": sys.version,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "pid": os.getpid(),
            "cwd": str(Path.cwd()),
            "environment": environment,
        }


def _safe_monotonic():
    try:
        import time
        return time.monotonic()
    except Exception:
        return None


def _serializable(value: Any, _depth: int = 0) -> Any:
    if _depth > 16:
        return "<maximum diagnostic depth reached>"
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _serializable(asdict(value), _depth + 1)
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = "<redacted>" if any(token in key_text.upper() for token in _SENSITIVE) else _serializable(item, _depth + 1)
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serializable(item, _depth + 1) for item in value]
    if isinstance(value, bytes):
        return "<%d bytes omitted>" % len(value)
    try:
        return {"type": "%s.%s" % (type(value).__module__, type(value).__name__), "repr": repr(value)}
    except Exception:
        return "<unrepresentable %s>" % type(value).__name__


def _redact_text(value: str) -> str:
    return re.sub(
        r"(?i)(token|password|passwd|secret|credential|api[_-]?key)(\s*[=:]\s*)([^\s;,]+)",
        lambda match: match.group(1) + match.group(2) + "<redacted>",
        value,
    )
