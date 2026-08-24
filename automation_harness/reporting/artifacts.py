from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from automation_harness.utils.evidence import EvidenceRecorder


@dataclass(frozen=True)
class RunArtifacts:
    root: Path
    logs: Path
    events: Path
    junit: Path
    stdout: Path
    stderr: Path
    run_json: Path
    summary: Path
    environment: Path

    @classmethod
    def create(cls, base: Path, label: str) -> "RunArtifacts":
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in label).strip("-") or "run"
        root = base / f"{stamp}-{safe_label}"
        logs = root / "logs"
        logs.mkdir(parents=True, exist_ok=False)
        return cls(
            root=root,
            logs=logs,
            events=root / "events.jsonl",
            junit=root / "junit.xml",
            stdout=root / "stdout.log",
            stderr=root / "stderr.log",
            run_json=root / "run.json",
            summary=root / "summary.txt",
            environment=root / "environment.json",
        )

    def recorder(self) -> EvidenceRecorder:
        return EvidenceRecorder(self.events)

    def write_run_json(self, payload: dict[str, Any]) -> None:
        self.run_json.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
