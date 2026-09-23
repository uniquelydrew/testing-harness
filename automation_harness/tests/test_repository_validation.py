from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.repository_validation import validate_repository
from automation_harness.models.component import ComponentDefinition, ComponentStrategy
from automation_harness.models.plan import StepCall, TestPlan


def _definition():
    return ComponentDefinition(
        component_id="file.menu", object_id="10000000-0000-0000-0000-000000000001",
        strategies=(ComponentStrategy("javafx", {"identification": {"mandatory": {"name": "File"}}}),),
    )


def test_rejects_transient_javafx_skin_and_empty_identity():
    definition = ComponentDefinition(
        component_id="menu.skin", object_id="10000000-0000-0000-0000-000000000001",
        framework="javafx", native_class="com.sun.javafx.scene.control.skin.MenuButtonSkin",
        strategies=(ComponentStrategy("javafx", {"identification": {"mandatory": {}}}),),
    )
    report = validate_repository(ComponentRepository({definition.component_id: definition}))
    assert not report.valid
    assert {item.code for item in report.errors} == {"transient_javafx_class", "empty_mandatory_locator"}


def test_plan_uuid_reference_is_valid_and_name_reference_is_rejected():
    definition = _definition()
    repository = ComponentRepository({definition.component_id: definition})
    uuid_plan = TestPlan("uuid", steps=(StepCall("one", "gui.action", {"component_id": definition.object_id}),))
    name_plan = TestPlan("name", steps=(StepCall("one", "gui.action", {"component_id": definition.component_id}),))
    assert validate_repository(repository, plan=uuid_plan).valid
    report = validate_repository(repository, plan=name_plan)
    assert not report.valid
    assert [item.code for item in report.issues] == ["mutable_name_reference"]


def test_dangling_plan_reference_is_an_error():
    definition = _definition()
    repository = ComponentRepository({definition.component_id: definition})
    plan = TestPlan("bad", steps=(StepCall("one", "gui.action", {"component_id": "missing"}),))
    report = validate_repository(repository, plan=plan)
    assert not report.valid
    assert report.errors[0].code == "dangling_plan_object_reference"
