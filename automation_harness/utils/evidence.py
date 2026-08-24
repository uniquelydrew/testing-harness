from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from automation_harness.models.run import utc_now


class EvidenceRecorder:
    """Append-only structured event recorder for a single run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, event: str, **fields: Any) -> None:
        payload = {"timestamp": utc_now(), "event": event, **fields}
        line = json.dumps(payload, sort_keys=True, default=str)
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
