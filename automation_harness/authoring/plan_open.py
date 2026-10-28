"""Non-mutating plan-open inspection before canonical repository composition.

Only linked/canonical plans with embedded definitions require explicit import.
An intentionally portable snapshot remains self-contained.
"""
from __future__ import annotations

from collections import namedtuple
from enum import Enum
from pathlib import Path

from automation_harness.authoring.plan_repository import assigned_repository_path
from automation_harness.core.test_plan import load_plan


class PlanOpenState(Enum):
    READY = "ready"
    MIGRATION_REQUIRED = "migration_required"


PlanOpenResult = namedtuple("PlanOpenResult", "plan state repository_path legacy_names")


def inspect_plan_open(path):
    path = Path(path)
    plan = load_plan(path)
    legacy = tuple(sorted(plan.objects)) if plan.execution_mode != "portable" else ()
    return PlanOpenResult(
        plan=plan,
        state=PlanOpenState.MIGRATION_REQUIRED if legacy else PlanOpenState.READY,
        repository_path=assigned_repository_path(plan, path),
        legacy_names=legacy,
    )
