from dataclasses import replace

import pytest

from automation_harness.authoring.step_order import StepOrderError, insert_calls, reorder_calls
from automation_harness.models.plan import StepCall, TestPlan


def _plan():
    return TestPlan(name="mixed", steps=(
        StepCall("recorded-1", "gui.object.action", group="recording"),
        StepCall("manual-1", "gui.object.exists.assert", depends_on=("recorded-1",), group="checks"),
        StepCall("recorded-2", "gui.object.action", group="recording"),
    ))


def test_insert_after_selection_preserves_existing_ids_and_groups():
    plan = insert_calls(_plan(), (StepCall("manual-2", "gui.object.exists.assert", group="checks"),),
                        after_node_id="recorded-1")
    assert [x.node_id for x in plan.steps] == ["recorded-1", "manual-2", "manual-1", "recorded-2"]
    assert plan.steps[1].group == "checks"


def test_recording_batch_preserves_internal_order():
    calls = (StepCall("recorded-3", "gui.object.action"), StepCall("recorded-4", "gui.object.action"))
    plan = insert_calls(_plan(), calls, after_node_id="manual-1")
    assert [x.node_id for x in plan.steps][2:4] == ["recorded-3", "recorded-4"]


def test_reorder_rejects_dependency_inversion():
    with pytest.raises(StepOrderError, match="must follow"):
        reorder_calls(_plan(), ("manual-1", "recorded-1", "recorded-2"))


def test_reorder_preserves_node_identity():
    plan = reorder_calls(_plan(), ("recorded-1", "recorded-2", "manual-1"))
    assert plan.steps[2].depends_on == ("recorded-1",)


def test_duplicate_or_missing_nodes_rejected():
    with pytest.raises(StepOrderError):
        reorder_calls(_plan(), ("recorded-1", "recorded-1", "manual-1"))
    with pytest.raises(StepOrderError):
        insert_calls(_plan(), (StepCall("manual-1", "gui.object.action"),))


def test_execution_queue_runs_in_authored_order():
    from automation_harness.core.test_plan import ManagedExecutionQueue
    queue = ManagedExecutionQueue(_plan())
    assert queue.ready() == ("recorded-1",)
    queue.start("recorded-1")
    queue.complete("recorded-1", {})
    assert queue.ready() == ("manual-1",)
    queue.start("manual-1")
    queue.complete("manual-1", {})
    assert queue.ready() == ("recorded-2",)


def test_execution_queue_does_not_skip_blocked_earlier_node():
    from automation_harness.core.test_plan import ManagedExecutionQueue
    plan = TestPlan(name="blocked", steps=(
        StepCall("first", "gui.object.action", depends_on=("later",)),
        StepCall("later", "gui.object.action"),
    ))
    queue = ManagedExecutionQueue(plan)
    assert queue.ready() == ()
