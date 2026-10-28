"""Non-mutating plan-open inspection before canonical repository composition.

Only linked/canonical plans with embedded definitions require explicit import.
An intentionally portable snapshot remains self-contained.
"""
from __future__ import annotations

from collections import namedtuple
from enum import Enum
from pathlib import Path

from automation_harness.authoring.diagnostic_events import artifact_fingerprint, emit
from automation_harness.authoring.plan_repository import assigned_repository_path
from automation_harness.core.test_plan import load_plan


class PlanOpenState(Enum):
    READY = "ready"
    MIGRATION_REQUIRED = "migration_required"


PlanOpenResult = namedtuple("PlanOpenResult", "plan state repository_path legacy_names")


def inspect_plan_open(path):
    path = Path(path)
    emit("plan_open_requested", plan_fingerprint=artifact_fingerprint(path))
    try:
        plan = load_plan(path)
    except Exception as exc:
        emit("plan_open_failed", error_type=type(exc).__name__)
        raise
    legacy = tuple(sorted(plan.objects)) if plan.execution_mode != "portable" else ()
    emit("legacy_detection_completed", legacy_count=len(legacy), execution_mode=plan.execution_mode,
         state="migration_required" if legacy else "ready")
    return PlanOpenResult(
        plan=plan,
        state=PlanOpenState.MIGRATION_REQUIRED if legacy else PlanOpenState.READY,
        repository_path=assigned_repository_path(plan, path),
        legacy_names=legacy,
    )
