"""Embedded legacy plans are inspected before repository composition."""
from uuid import uuid4

from automation_harness.authoring.plan_open import inspect_plan_open, PlanOpenState
from automation_harness.core.test_plan import save_plan
from automation_harness.models.plan import TestPlan


def test_clean_plan_does_not_require_migration(tmp_path):
    path = tmp_path / "clean.ahplan"
    save_plan(TestPlan(name="clean"), path)
    result = inspect_plan_open(path)
    assert result.state is PlanOpenState.READY
    assert result.legacy_names == ()


def test_linked_embedded_plan_is_detected_without_loading_missing_repository(tmp_path):
    path = tmp_path / "legacy.ahplan"
    uid = str(uuid4())
    plan = TestPlan(name="legacy", objects={
        "Main": {"object_id": uid, "object_type": "custom",
                 "strategies": [{"type": "atspi", "options": {"identification": {"mandatory": {"name": "Main"}}}}]},
    })
    save_plan(plan, path)
    result = inspect_plan_open(path)
    assert result.state is PlanOpenState.MIGRATION_REQUIRED
    assert result.legacy_names == ("Main",)
    assert result.plan.objects["Main"]["object_id"] == uid


def test_portable_embedded_snapshot_is_not_forced_into_canonical_migration(tmp_path):
    path = tmp_path / "portable.ahplan"
    plan = TestPlan(name="portable", execution_mode="portable", objects={"Main": {"object_id": str(uuid4())}})
    save_plan(plan, path)
    result = inspect_plan_open(path)
    assert result.state is PlanOpenState.READY
    assert result.plan.objects


def test_legacy_preflight_is_nonmutating(tmp_path):
    path = tmp_path / "legacy.ahplan"
    save_plan(TestPlan(name="legacy", objects={"A": {"object_id": str(uuid4())}}), path)
    before = path.read_bytes()
    inspect_plan_open(path)
    assert path.read_bytes() == before


def test_authoring_window_gates_unmigrated_legacy_without_composing_repository():
    from pathlib import Path
    base = Path(__file__).resolve().parents[1] / "authoring" / "gui"
    body = (base / "plan_window.py").read_text(encoding="utf-8")
    authoring = (base / "plan_authoring_window.py").read_text(encoding="utf-8")
    assert "if self._defer_repository_composition:" in body
    assert "if self._legacy_pending:" in authoring
    assert "self._defer_repository_composition = False" in authoring
