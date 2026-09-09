import pytest

from automation_harness.core.reusable_step_expansion import (
    ReusableStepExpansionError,
    expand_reusable_steps,
)
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.models.plan import PlanVariableRef, StepCall, TestPlan


def _login_definition():
    return ReusableStepDefinition(
        step_id="authentication.login",
        name="Login",
        description="",
        inputs={
            "username": {"type": "str", "required": True},
            "options": {"type": "dict", "required": False, "default": {"timeout": 5}},
        },
        outputs={"authenticated_user": "authenticated_user"},
        plan=TestPlan(
            name="Login",
            steps=(
                StepCall(
                    "enter",
                    "gui.object.action",
                    inputs={
                        "value": PlanVariableRef("username"),
                        "timeout": PlanVariableRef("options.timeout"),
                    },
                    outputs={"value": "entered_username"},
                ),
                StepCall(
                    "submit",
                    "gui.object.action",
                    inputs={"entered": PlanVariableRef("entered_username")},
                    outputs={"user": "authenticated_user"},
                    depends_on=("enter",),
                ),
            ),
        ),
    )


def test_expand_reusable_call_namespaces_nodes_and_internal_variables():
    plan = TestPlan(
        name="Test",
        steps=(
            StepCall("prepare", "environment.prepare"),
            StepCall(
                "login",
                "authentication.login",
                inputs={"username": PlanVariableRef("test_user")},
                outputs={"authenticated_user": "current_user"},
                depends_on=("prepare",),
                group="Authentication",
            ),
            StepCall(
                "verify",
                "validation.equals",
                inputs={"actual": PlanVariableRef("current_user")},
                depends_on=("login",),
            ),
        ),
    )

    expanded = expand_reusable_steps(plan, {"authentication.login": _login_definition()})

    assert [call.node_id for call in expanded.steps] == [
        "prepare", "login::enter", "login::submit", "verify"
    ]
    enter, submit = expanded.steps[1:3]
    assert enter.depends_on == ("prepare",)
    assert enter.inputs["value"] == PlanVariableRef("test_user")
    assert enter.inputs["timeout"] == 5
    assert enter.outputs == {"value": "login::entered_username"}
    assert enter.group == "Authentication"
    assert submit.depends_on == ("login::enter",)
    assert submit.inputs["entered"] == PlanVariableRef("login::entered_username")
    assert submit.outputs == {"user": "current_user"}
    assert expanded.steps[3].depends_on == ("login::submit",)


def test_expand_reusable_call_requires_declared_inputs():
    plan = TestPlan(
        name="Test",
        steps=(StepCall("login", "authentication.login"),),
    )

    with pytest.raises(ReusableStepExpansionError, match="missing required input 'username'"):
        expand_reusable_steps(plan, {"authentication.login": _login_definition()})


def test_expand_reusable_call_rejects_unknown_output_binding():
    plan = TestPlan(
        name="Test",
        steps=(StepCall(
            "login",
            "authentication.login",
            inputs={"username": "alice"},
            outputs={"missing": "value"},
        ),),
    )

    with pytest.raises(ReusableStepExpansionError, match="has no output"):
        expand_reusable_steps(plan, {"authentication.login": _login_definition()})


def test_expand_reusable_steps_recursively_and_detects_cycles():
    first = ReusableStepDefinition(
        "first", "First", "",
        TestPlan("First", steps=(StepCall("nested", "second"),)),
        {}, {},
    )
    second = ReusableStepDefinition(
        "second", "Second", "",
        TestPlan("Second", steps=(StepCall("leaf", "primitive"),)),
        {}, {},
    )

    expanded = expand_reusable_steps(
        TestPlan("Test", steps=(StepCall("outer", "first"),)),
        {"first": first, "second": second},
    )
    assert [call.node_id for call in expanded.steps] == ["outer::nested::leaf"]
    assert expanded.steps[0].step_id == "primitive"

    cyclic_second = ReusableStepDefinition(
        "second", "Second", "",
        TestPlan("Second", steps=(StepCall("back", "first"),)),
        {}, {},
    )
    with pytest.raises(ReusableStepExpansionError, match="reusable step cycle"):
        expand_reusable_steps(
            TestPlan("Test", steps=(StepCall("outer", "first"),)),
            {"first": first, "second": cyclic_second},
        )
