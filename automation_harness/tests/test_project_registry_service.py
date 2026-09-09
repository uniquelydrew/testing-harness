from automation_harness.authoring.project import AuthoringProject, create_authoring_project
from automation_harness.authoring.project_registry_service import save_plan_selection_to_project_registry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.plan import StepCall, TestPlan


def _repository():
    return ComponentRepository.from_document({
        "version": 2,
        "components": {
            "login.submit": {
                "object_type": "button",
                "actions": ["click"],
                "strategies": [{"type": "atspi", "name": "Submit", "role": "push button"}],
            }
        },
    })


def test_saving_to_new_project_registry_adds_registry_and_repository_membership(tmp_path):
    project_path = tmp_path / "suite.ahproject"
    create_authoring_project(project_path, "Suite")
    registry_path = tmp_path / "common.ahregistry"
    plan = TestPlan(
        name="Login",
        steps=(StepCall(
            "submit",
            "gui.object.action",
            inputs={"component_id": "login.submit"},
            group="Login",
        ),),
    )

    project, registry = save_plan_selection_to_project_registry(
        project_path,
        plan,
        source_repository=_repository(),
        registry_path=registry_path,
        create_registry_name="Common",
        step_id="authentication.login",
        name="Login",
        group="Login",
    )

    reloaded = AuthoringProject.load(project_path)
    assert registry_path.resolve() in project.step_registries
    assert registry.repository in project.object_repositories
    assert reloaded.step_registries == project.step_registries
    assert reloaded.object_repositories == project.object_repositories


def test_saving_again_does_not_duplicate_project_membership(tmp_path):
    project_path = tmp_path / "suite.ahproject"
    create_authoring_project(project_path, "Suite")
    registry_path = tmp_path / "common.ahregistry"
    plan = TestPlan(
        name="Login",
        steps=(StepCall("one", "noop", group="Reusable"),),
    )

    for _ in range(2):
        project, _registry = save_plan_selection_to_project_registry(
            project_path,
            plan,
            source_repository=ComponentRepository({}),
            registry_path=registry_path,
            create_registry_name="Common",
            step_id="example.one",
            name="One",
            group="Reusable",
        )

    assert len(project.step_registries) == 1
    assert len(project.object_repositories) == 1
