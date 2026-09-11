import pytest

from automation_harness.authoring.reusable_extraction import (
    ReusableExtractionError,
    add_extracted_step_to_registry,
    extract_reusable_step,
    reconcile_registry_objects,
    save_plan_selection_to_registry,
)
from automation_harness.authoring.step_registry import AuthoringStepRegistry, create_step_registry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan


def _repository(component_id="login.submit"):
    return ComponentRepository.from_document({
        "version": 2,
        "components": {
            component_id: {
                "object_type": "button",
                "actions": ["click"],
                "strategies": [
                    {"type": "atspi", "name": "Submit", "role": "push button"}
                ],
            }
        },
    })


def test_extract_group_preserves_internal_dependencies_and_infers_io():
    plan = TestPlan(
        name="Login test",
        variables={"username": "alice"},
        steps=(
            StepCall("launch", "environment.prepare", outputs={"ready": "ready"}),
            StepCall(
                "enter",
                "gui.object.action",
                inputs={
                    "component_id": "login.username",
                    "value": PlanVariableRef("username"),
                    "gate": PlanVariableRef("ready"),
                },
                depends_on=("launch",),
                outputs={"value": "entered_username"},
                group="Login",
            ),
            StepCall(
                "submit",
                "gui.object.action",
                inputs={"component_id": "login.submit"},
                depends_on=("enter",),
                outputs={"result": "authenticated_user"},
                group="Login",
            ),
            StepCall(
                "verify",
                "validation.equals",
                inputs={"actual": PlanVariableRef("authenticated_user")},
                depends_on=("submit",),
            ),
        ),
    )

    definition = extract_reusable_step(
        plan,
        step_id="authentication.login",
        name="Login",
        group="Login",
    )

    assert [step.node_id for step in definition.plan.steps] == ["enter", "submit"]
    assert definition.plan.steps[0].depends_on == ()
    assert definition.plan.steps[1].depends_on == ("enter",)
    assert all(step.group == "" for step in definition.plan.steps)
    assert definition.inputs["username"] == {
        "type": "str",
        "required": False,
        "default": "alice",
    }
    assert definition.inputs["ready"] == {"type": "any", "required": True}
    assert definition.outputs == {"authenticated_user": "authenticated_user"}
    assert definition.plan.variables == {"username": "alice"}


def test_extract_explicit_nodes_preserves_plan_order():
    plan = TestPlan(
        name="Example",
        steps=(
            StepCall("a", "one"),
            StepCall("b", "two"),
            StepCall("c", "three"),
        ),
    )

    definition = extract_reusable_step(
        plan,
        step_id="example.partial",
        name="Partial",
        node_ids=("c", "a"),
    )

    assert [step.node_id for step in definition.plan.steps] == ["a", "c"]


def test_extract_requires_a_nonempty_selection():
    plan = TestPlan(name="Empty")

    with pytest.raises(ReusableExtractionError, match="requires node_ids or group"):
        extract_reusable_step(plan, step_id="x", name="X")


def test_reconcile_copies_only_referenced_objects():
    source = _repository("login.submit")
    definition = extract_reusable_step(
        TestPlan(
            name="Login",
            steps=(StepCall(
                "submit",
                "gui.object.action",
                inputs={"component_id": "login.submit"},
                group="Login",
            ),),
        ),
        step_id="authentication.login",
        name="Login",
        group="Login",
    )

    reconciled = reconcile_registry_objects(
        definition,
        source_repository=source,
        target_repository=ComponentRepository({}),
    )

    assert reconciled.contains("login.submit")
    assert len(reconciled.components) == 1


def test_reconcile_rejects_conflicting_immutable_identity():
    source = ComponentRepository.from_document({
        "version": 3,
        "components": {
            "login.submit": {
                "object_id": "00000000-0000-0000-0000-000000000001",
                "object_type": "button",
                "actions": ["click"],
                "strategies": [{"type": "atspi", "name": "Submit", "role": "push button"}],
            }
        },
    })
    target = ComponentRepository.from_document({
        "version": 3,
        "components": {
            "login.submit": {
                "object_id": "00000000-0000-0000-0000-000000000002",
                "object_type": "button",
                "actions": ["click"],
                "strategies": [{"type": "atspi", "name": "Submit", "role": "push button"}],
            }
        },
    })
    definition = extract_reusable_step(
        TestPlan(
            name="Login",
            steps=(StepCall(
                "submit",
                "gui.object.action",
                inputs={"component_id": "login.submit"},
                group="Login",
            ),),
        ),
        step_id="authentication.login",
        name="Login",
        group="Login",
    )

    with pytest.raises(ReusableExtractionError, match="immutable object id"):
        reconcile_registry_objects(
            definition,
            source_repository=source,
            target_repository=target,
        )


def test_add_extracted_step_updates_registry_and_repository(tmp_path):
    registry = create_step_registry(tmp_path / "common", "Common")
    source = _repository("login.submit")
    definition = extract_reusable_step(
        TestPlan(
            name="Login",
            steps=(StepCall(
                "submit",
                "gui.object.action",
                inputs={"component_id": "login.submit"},
                group="Login",
            ),),
        ),
        step_id="authentication.login",
        name="Login",
        group="Login",
    )

    updated, repository = add_extracted_step_to_registry(
        registry,
        definition,
        source_repository=source,
    )

    assert updated.get("authentication.login") == definition
    assert repository.contains("login.submit")


def test_save_plan_selection_creates_registry_and_persists_referenced_objects(tmp_path):
    path = tmp_path / "common.ahregistry"
    source = _repository("login.submit")
    plan = TestPlan(
        name="Login",
        steps=(StepCall(
            "submit",
            "gui.object.action",
            inputs={"component_id": "login.submit"},
            group="Login",
        ),),
    )

    saved = save_plan_selection_to_registry(
        plan,
        source_repository=source,
        registry_path=path,
        create_registry_name="Common",
        step_id="authentication.login",
        name="Login",
        group="Login",
    )

    loaded = AuthoringStepRegistry.load(path)
    repository = ComponentRepository.load((loaded.repository,))
    assert saved.get("authentication.login").name == "Login"
    assert loaded.get("authentication.login").plan.steps[0].node_id == "submit"
    assert repository.contains("login.submit")


def test_save_plan_selection_requires_name_when_creating_registry(tmp_path):
    with pytest.raises(ReusableExtractionError, match="create_registry_name"):
        save_plan_selection_to_registry(
            TestPlan(name="Example", steps=(StepCall("one", "noop"),)),
            source_repository=ComponentRepository({}),
            registry_path=tmp_path / "missing.ahregistry",
            step_id="example.one",
            name="One",
            node_ids=("one",),
        )
