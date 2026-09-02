from __future__ import annotations

from pathlib import Path

from automation_harness.core.step_registry import default_step_registry
from automation_harness.core.test_plan import derive_execution_state, load_plan, save_plan, validate_plan
from automation_harness.models.plan import PlanVariableRef, StepCall, StepStatus, TestPlan


def test_plan_round_trip_and_dataflow_queue(tmp_path: Path):
    plan = TestPlan(
        name="dataflow",
        variables={"track_name": "alpha"},
        steps=(
            StepCall(
                node_id="create",
                step_id="track.create_moving",
                inputs={"track_id": PlanVariableRef("track_name")},
                outputs={"track_id": "active_track"},
            ),
            StepCall(
                node_id="follow",
                step_id="track.follow",
                inputs={"track_id": PlanVariableRef("active_track")},
            ),
        ),
    )
    assert validate_plan(plan, default_step_registry()) == []
    state = derive_execution_state(plan)
    assert state.steps["create"].status is StepStatus.READY
    assert state.steps["follow"].status is StepStatus.BLOCKED
    assert state.steps["follow"].unresolved_variables == ("active_track",)

    path = tmp_path / "plan.yaml"
    save_plan(plan, path)
    loaded = load_plan(path)
    assert loaded == plan


def test_plan_round_trip_preserves_inline_objects_and_step_definitions(tmp_path: Path):
    plan = TestPlan(
        name="self-contained",
        objects={
            "submit": {
                "object_type": "button",
                "actions": ["click"],
                "strategies": [
                    {
                        "type": "atspi",
                        "identification": {
                            "mandatory": {
                                "name": {"match": "regex", "value": "Submit.*"}
                            }
                        },
                    }
                ],
            }
        },
        step_definitions={
            "gui.object.action": {
                "name": "gui.object.action",
                "description": "Execute an object action.",
            }
        },
        steps=(
            StepCall(
                node_id="click-submit",
                step_id="gui.object.action",
                inputs={"component_id": "submit", "action": {"type": "click"}},
            ),
        ),
    )
    path = tmp_path / "self-contained.yaml"
    save_plan(plan, path)
    assert load_plan(path) == plan


def test_plan_validation_catches_unknown_step_and_output():
    plan = TestPlan(
        name="bad",
        steps=(
            StepCall(node_id="one", step_id="track.folow", inputs={"track_id": "x"}),
            StepCall(node_id="two", step_id="track.follow", inputs={"track_id": "x"}, outputs={"wat": "x"}),
        ),
    )
    issues = validate_plan(plan, default_step_registry())
    assert any("track.folow" in issue for issue in issues)
    assert any("has no output 'wat'" in issue for issue in issues)


def test_managed_queue_releases_consumer_after_output_commit():
    from automation_harness.core.test_plan import ManagedExecutionQueue

    plan = TestPlan(
        name="queue",
        variables={"requested": "alpha"},
        steps=(
            StepCall(
                node_id="create",
                step_id="track.create_moving",
                inputs={"track_id": PlanVariableRef("requested")},
                outputs={"track_id": "active"},
            ),
            StepCall(
                node_id="follow",
                step_id="track.follow",
                inputs={"track_id": PlanVariableRef("active")},
            ),
        ),
    )
    queue = ManagedExecutionQueue(plan)
    assert queue.ready() == ("create",)
    queue.start("create", {"track_id": "alpha"})
    queue.complete("create", {"track_id": "alpha"})
    assert queue.ready() == ("follow",)
    assert queue.state.variables["active"] == "alpha"


def test_plan_allows_reference_to_later_unique_producer_and_blocks_consumer():
    plan = TestPlan(
        name="later-producer",
        steps=(
            StepCall(
                node_id="consume",
                step_id="track.follow",
                inputs={"track_id": PlanVariableRef("active")},
            ),
            StepCall(
                node_id="produce",
                step_id="track.create_moving",
                outputs={"track_id": "active"},
            ),
        ),
    )
    assert validate_plan(plan, default_step_registry()) == []
    state = derive_execution_state(plan)
    assert state.steps["consume"].status is StepStatus.BLOCKED
    assert state.steps["produce"].status is StepStatus.READY


def test_plan_validation_rejects_dependency_cycle():
    plan = TestPlan(
        name="cycle",
        steps=(
            StepCall(node_id="one", step_id="validation.equal", inputs={"name": "a", "actual": 1, "expected": 1}, depends_on=("two",)),
            StepCall(node_id="two", step_id="validation.equal", inputs={"name": "b", "actual": 1, "expected": 1}, depends_on=("one",)),
        ),
    )
    issues = validate_plan(plan, default_step_registry())
    assert any("execution dependency cycle" in issue for issue in issues)


def test_nested_initial_variable_path_must_exist():
    plan = TestPlan(
        name="nested-missing",
        variables={"session": {}},
        steps=(
            StepCall(
                node_id="check",
                step_id="validation.equal",
                inputs={"name": "nested", "actual": PlanVariableRef("session.value"), "expected": 1},
            ),
        ),
    )
    issues = validate_plan(plan, default_step_registry())
    assert any("missing nested variable path 'session.value'" in issue for issue in issues)
    state = derive_execution_state(plan)
    assert state.steps["check"].status is StepStatus.BLOCKED
    assert state.steps["check"].unresolved_variables == ("session.value",)


def test_nested_path_becomes_ready_after_root_producer_commits_mapping():
    from automation_harness.core.test_plan import ManagedExecutionQueue

    plan = TestPlan(
        name="nested-produced",
        steps=(
            StepCall(
                node_id="consume",
                step_id="validation.equal",
                inputs={"name": "nested", "actual": PlanVariableRef("record.track_id"), "expected": "alpha"},
            ),
            StepCall(
                node_id="produce",
                step_id="track.create_moving",
                inputs={"track_id": "alpha"},
                outputs={"track": "record"},
            ),
        ),
    )
    assert validate_plan(plan, default_step_registry()) == []
    queue = ManagedExecutionQueue(plan)
    assert queue.state.steps["consume"].status is StepStatus.BLOCKED
    queue.start("produce", {"track_id": "alpha"})
    queue.complete("produce", {"track": {"track_id": "alpha"}})
    assert queue.state.steps["consume"].status is StepStatus.READY


def test_plan_validation_rejects_obviously_wrong_literal_input_type():
    plan = TestPlan(
        name="bad-type",
        steps=(
            StepCall(
                node_id="create",
                step_id="track.create_moving",
                inputs={"track_id": "alpha", "x": "not-a-number"},
            ),
        ),
    )
    issues = validate_plan(plan, default_step_registry())
    assert any("input 'x'" in issue and "expects float" in issue for issue in issues)


def test_plan_validation_accepts_integer_literal_for_float_input():
    plan = TestPlan(
        name="numeric-compatible",
        steps=(
            StepCall(
                node_id="create",
                step_id="track.create_moving",
                inputs={"track_id": "alpha", "x": 12},
            ),
        ),
    )
    assert validate_plan(plan, default_step_registry()) == []
