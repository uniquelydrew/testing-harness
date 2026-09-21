from pathlib import Path

from automation_harness.authoring.plan_repository import (
    assigned_repository_path,
    assign_repository,
    ensure_default_repository,
    materialize_captured_target,
    merge_objects_or,
)
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.test_plan import load_plan, save_plan
from automation_harness.models.component import CapturedComponent, ComponentDefinition, ComponentState, ComponentStrategy
from automation_harness.models.gui import ObjectType
from automation_harness.models.plan import TestPlan


def _capture(name="Submit", accessible_id="submit"):
    return CapturedComponent(
        name=name,
        role="push button",
        description="Submit button",
        accessible_id=accessible_id,
        application="demo",
        hierarchy=("window", "button"),
        actions=("activate",),
        bounds=(10, 20, 100, 30),
        state=ComponentState(present=True),
        window="Demo",
        object_type=ObjectType.BUTTON,
        framework="atspi",
    )


def _runtime_track(track_ref="track-a"):
    return CapturedComponent(
        name="track@%s" % track_ref,
        role="rendered_object",
        description=None,
        accessible_id=None,
        application="MSCT Domain 12",
        window="MSCT Domain 12",
        hierarchy=("AWTViewCanvas", "DefaultTrackVelocityDisplay2D"),
        actions=("resolve", "click"),
        bounds=(100, 200, 13, 13),
        state=ComponentState(present=True, visible=True, showing=True),
        backend_properties={
            "track_runtime_ref": track_ref,
            "rendered_object_ref": "display-" + track_ref,
            "identity_state": "runtime_only",
        },
        authored_strategy=ComponentStrategy("java_agent", {
            "runtime_correlation": {
                "track_runtime_ref": track_ref,
                "rendered_object_ref": "display-" + track_ref,
                "window": "MSCT Domain 12",
            },
        }),
        object_type=ObjectType.CUSTOM,
        framework="solipsys_rendered",
        native_class="com.solipsys.tdf.track.DefaultTrackVelocityDisplay2D",
    )


def test_repository_assignment_round_trips_through_plan_metadata(tmp_path):
    plan_path = tmp_path / "login.ahplan"
    repository_path = tmp_path / "objects.ahobjects"
    ComponentRepository({}).save(repository_path)
    plan = assign_repository(TestPlan(name="login"), plan_path, repository_path)
    save_plan(plan, plan_path)

    loaded = load_plan(plan_path)
    assert assigned_repository_path(loaded, plan_path) == repository_path.resolve()


def test_default_repository_is_created_next_to_plan(tmp_path):
    plan_path = tmp_path / "login.ahplan"
    plan, repository_path = ensure_default_repository(TestPlan(name="login"), plan_path)
    assert repository_path == tmp_path / "login.ahobjects"
    assert repository_path.is_file()
    assert assigned_repository_path(plan, plan_path) == repository_path.resolve()


def test_repeated_capture_reuses_same_repository_object():
    repository = ComponentRepository({})
    repository, first_id, created = materialize_captured_target(repository, _capture())
    assert created is True
    repository, second_id, created = materialize_captured_target(repository, _capture())
    assert created is False
    assert second_id == first_id
    assert len(repository.components) == 1


def test_runtime_only_solipsys_track_materializes_as_visible_provisional_object():
    repository, component_id, created = materialize_captured_target(
        ComponentRepository({}), _runtime_track(),
    )

    assert created is True
    definition = repository.get(component_id)
    assert definition.framework == "solipsys_rendered"
    assert definition.native_class.endswith("DefaultTrackVelocityDisplay2D")
    assert definition.properties["locator_status"] == "provisional"
    assert definition.properties["cross_session_resolution"] is False
    assert definition.strategies[0].options["runtime_correlation"]["track_runtime_ref"] == "track-a"


def test_runtime_only_solipsys_track_reuses_same_runtime_object_but_not_distinct_track():
    repository = ComponentRepository({})
    repository, first_id, _created = materialize_captured_target(repository, _runtime_track("track-a"))
    repository, repeated_id, repeated_created = materialize_captured_target(repository, _runtime_track("track-a"))
    repository, second_id, second_created = materialize_captured_target(repository, _runtime_track("track-b"))

    assert repeated_created is False
    assert repeated_id == first_id
    assert second_created is True
    assert second_id != first_id
    assert len(repository.components) == 2


def test_or_merge_unions_locator_strategies_and_removes_duplicate():
    one = ComponentDefinition(
        "submit",
        strategies=(ComponentStrategy("atspi", {"identification": {"mandatory": {"accessible_id": "submit"}}}),),
        object_type=ObjectType.BUTTON,
        framework="atspi",
    )
    two = ComponentDefinition(
        "submit-alt",
        strategies=(ComponentStrategy("javafx", {"identification": {"fx_id": "submit"}}),),
        object_type=ObjectType.BUTTON,
        framework="javafx",
    )
    repository = ComponentRepository({"submit": one, "submit-alt": two})
    merged = merge_objects_or(repository, "submit", ("submit-alt",))
    assert "submit-alt" not in merged.components
    assert len(merged.get("submit").strategies) == 2
    assert merged.get("submit").object_id == one.object_id
