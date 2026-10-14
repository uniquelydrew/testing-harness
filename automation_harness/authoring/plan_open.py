"""Preflight legacy detection before the GTK editor resolves repositories."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from automation_harness.authoring.diagnostic_events import emit, artifact_fingerprint
from automation_harness.authoring.plan_repository import assigned_repository_path
from automation_harness.core.test_plan import load_plan


class PlanOpenState(Enum):
    READY = "ready"
    MIGRATION_REQUIRED = "migration_required"


@dataclass(frozen=True)
class PlanOpenResult:
    plan: object
    state: PlanOpenState
    repository_path: Path | None
    legacy_names: tuple[str, ...]


def inspect_plan_open(path):
    """Read the artifact without loading or composing its assigned OR.

    Conflict classification is deliberately deferred to the migration session:
    a corrupt or missing assigned OR must not prevent legacy detection.
    """
    path = Path(path)
    emit('plan_open_requested', plan=str(path), fingerprint=artifact_fingerprint(path))
    try:
        plan = load_plan(path)
    except Exception as exc:
        emit('plan_open_failed', plan=str(path), error_type=type(exc).__name__, error=str(exc))
        raise
    names = tuple(sorted(plan.objects))
    emit('legacy_detection_completed', plan=str(path), legacy_count=len(names), legacy_names=names)
    return PlanOpenResult(
        plan=plan,
        state=PlanOpenState.MIGRATION_REQUIRED if names else PlanOpenState.READY,
        repository_path=assigned_repository_path(plan, path),
        legacy_names=names,
    )
