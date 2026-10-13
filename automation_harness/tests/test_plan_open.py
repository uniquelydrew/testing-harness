"""Plan-open preflight must detect legacy objects without composing repositories."""
from uuid import uuid4

from automation_harness.authoring.plan_open import inspect_plan_open, PlanOpenState
from automation_harness.core.test_plan import save_plan
from automation_harness.models.plan import TestPlan


def test_clean_plan_opens_ready(tmp_path):
    path = tmp_path / "clean.ahplan"
    save_plan(TestPlan(name="clean"), path)
    result = inspect_plan_open(path)
    assert result.state is PlanOpenState.READY
    assert result.legacy_names == ()


def test_legacy_plan_requires_migration_before_repository_resolution(tmp_path):
    path = tmp_path / "legacy.ahplan"
    uid = str(uuid4())
    plan = TestPlan(name="legacy", objects={
        "Main Window": {
            "object_id": uid,
            "object_type": "custom",
            "actions": ["resolve"],
            "strategies": [{"driver": "atspi", "selector": {"name": "Main Window"}}],
        }
    })
    save_plan(plan, path)
    result = inspect_plan_open(path)
    assert result.state is PlanOpenState.MIGRATION_REQUIRED
    assert result.legacy_names == ("Main Window",)
    assert result.plan.objects["Main Window"]["object_id"] == uid
