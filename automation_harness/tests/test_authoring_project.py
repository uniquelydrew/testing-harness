from automation_harness.authoring.project import AuthoringProject, applications_for_plan, create_authoring_project
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.plan import StepCall, TestPlan


def test_attached_project_round_trip_and_creates_empty_repository(tmp_path):
    path = tmp_path / "project.yaml"
    created = create_authoring_project(path, "Authoring smoke test")
    loaded = AuthoringProject.load(path)
    assert loaded.name == created.name
    assert loaded.repository.is_file()
    assert loaded.target["kind"] == "attached-desktop"


def test_project_resolves_paths_relative_to_manifest(tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text("""version: 1
name: Example
repository: assets/components.yaml
runs_dir: evidence
target:
  kind: reference
  display: auto
""", encoding="utf-8")
    project = AuthoringProject.load(path)
    assert project.repository == (tmp_path / "assets/components.yaml").resolve()
    assert project.runs_dir == (tmp_path / "evidence").resolve()


def test_new_project_does_not_default_to_obsolete_reference_target(tmp_path):
    project = create_authoring_project(tmp_path / "project.yaml", "Live target")
    assert project.target == {"kind": "attached-desktop"}


def test_existing_captured_objects_can_infer_attached_application():
    repository = ComponentRepository.from_document({"version": 2, "components": {"show-all": {
        "object_type": "button", "actions": ["click"],
        "strategies": [{"type": "atspi", "identification": {
            "mandatory": {"name": "Show All"}, "assistive": {"application": "ERSA"},
        }}],
    }}})
    plan = TestPlan("click", steps=(StepCall("step-001", "gui.object.action", {
        "component_id": "show-all", "action": {"type": "click"},
    }),))
    assert applications_for_plan(plan, repository) == frozenset({"ERSA"})
