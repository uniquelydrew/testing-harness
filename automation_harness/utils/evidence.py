from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping

from automation_harness.models.evidence import EvidenceItem
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

    def record_assertion(
        self,
        assertion: str,
        *,
        passed: bool,
        evidence: Iterable[EvidenceItem | Mapping[str, Any]],
        node_id: str | None = None,
        component_id: str | None = None,
        expected: Any = None,
        actual: Any = None,
        message: str | None = None,
        **fields: Any,
    ) -> None:
        """Record one assertion with explicit expected and actual evidence.

        ``expected`` and ``actual`` remain as compact compatibility fields while
        reports consume the richer typed evidence collection.
        """
        normalized = []
        roles = set()
        for item in evidence:
            if isinstance(item, EvidenceItem):
                payload = item.to_dict()
            elif isinstance(item, Mapping):
                payload = dict(item)
            else:
                raise TypeError("assertion evidence must contain EvidenceItem or mapping values")
            role = payload.get("role")
            evidence_type = payload.get("type")
            if not isinstance(role, str) or not isinstance(evidence_type, str):
                raise ValueError("assertion evidence requires string role and type fields")
            roles.add(role)
            normalized.append(payload)
        missing = {"expected", "actual"} - roles
        if missing:
            raise ValueError(
                "assertion evidence is missing required role(s): %s"
                % ", ".join(sorted(missing))
            )
        payload_fields = dict(fields)
        payload_fields.update(
            {
                "assertion": assertion,
                "passed": bool(passed),
                "expected": expected,
                "actual": actual,
                "evidence": normalized,
            }
        )
        if node_id is not None:
            payload_fields["node_id"] = node_id
        if component_id is not None:
            payload_fields["component_id"] = component_id
        if message:
            payload_fields["message"] = message
        self.record("assertion", **payload_fields)
