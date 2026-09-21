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
_MAX_DEPTH = 8
_MAX_MAPPING_ITEMS = 96
_MAX_SEQUENCE_ITEMS = 64
_MAX_TEXT_LENGTH = 4096
_OMITTED_DETAIL_KEYS = frozenset({
    # These reflective graphs repeat for every pointer observation and can
    # expand a short recording into hundreds of megabytes.  Semantic capture
    # summaries are logged separately by RecordingSession.
    "render_surface_inspection",
    "backing_objects",
    "candidate_methods",
    "selection_manager_state",
})


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
    if _depth > _MAX_DEPTH:
        return "<maximum diagnostic depth reached>"
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        redacted = _redact_text(value)
        if len(redacted) > _MAX_TEXT_LENGTH:
            return redacted[:_MAX_TEXT_LENGTH] + "<%d characters omitted>" % (
                len(redacted) - _MAX_TEXT_LENGTH
            )
        return redacted
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _serializable(asdict(value), _depth + 1)
    if isinstance(value, Mapping):
        result = {}
        items = list(value.items())
        for key, item in items[:_MAX_MAPPING_ITEMS]:
            key_text = str(key)
            if key_text in _OMITTED_DETAIL_KEYS:
                result[key_text] = "<verbose reflective detail omitted>"
            else:
                result[key_text] = "<redacted>" if any(token in key_text.upper() for token in _SENSITIVE) else _serializable(item, _depth + 1)
        if len(items) > _MAX_MAPPING_ITEMS:
            result["<truncated>"] = "%d mapping entries omitted" % (
                len(items) - _MAX_MAPPING_ITEMS
            )
        return result
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        result = [_serializable(item, _depth + 1) for item in items[:_MAX_SEQUENCE_ITEMS]]
        if len(items) > _MAX_SEQUENCE_ITEMS:
            result.append("<%d sequence entries omitted>" % (len(items) - _MAX_SEQUENCE_ITEMS))
        return result
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
