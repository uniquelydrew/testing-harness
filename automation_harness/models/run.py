from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class BackendHealth:
    healthy: bool
    backend: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult:
    run_id: str
    backend: str
    bundle: str
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    artifact_dir: Path | None = None
    validation_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.artifact_dir is not None:
            data["artifact_dir"] = str(self.artifact_dir)
        return data
