from pathlib import Path

import pytest
import yaml

from automation_harness.authoring.project import (
    AuthoringProject,
    ProjectError,
    applications_for_plan,
    create_authoring_project,
)
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.script_steps import registered_script_step
from automation_harness.models.plan import StepCall, TestPlan


def test_project_round_trip_has_no_persisted_target(tmp_path):
    path = tmp_path / "project.yaml"
    created = create_authoring_project(path, "Authoring smoke test")
    loaded = AuthoringProject.load(path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert loaded.name == created.name
    assert loaded.repository.is_file()
    assert loaded.target == {}
    assert "target" not in document
    assert "environment_script" not in document
    assert loaded.backend().name == "live-desktop"


def test_project_resolves_paths_and_loads_script_step_manifests(tmp_path):
    script = tmp_path / "scripts" / "prepare.py"
    script.parent.mkdir()
    script.write_text(
        "import json, sys\nrequest = json.load(sys.stdin)\njson.dump({'protocol': 1, 'outputs': {'ready': True}}, sys.stdout)\n",
        encoding="utf-8",
    )
    manifest = script.parent / "prepare.yaml"
    manifest.write_text(
        """version: 1
id: environment.project_prepare
outputs:
  ready: bool
implementation:
  kind: script
  path: prepare.py
  interpreter: python
""",
        encoding="utf-8",
    )
    path = tmp_path / "project.yaml"
    path.write_text(
        """version: 1
name: Example
repository: assets/components.yaml
runs_dir: evidence
script_steps:
  - scripts/prepare.yaml
target:
  kind: reference
  display: auto
""",
        encoding="utf-8",
    )
    project = AuthoringProject.load(path)
    assert project.repository == (tmp_path / "assets/components.yaml").resolve()
    assert project.runs_dir == (tmp_path / "evidence").resolve()
    assert project.script_steps == (manifest.resolve(),)
    assert project.backend().name == "live-desktop"
    assert registered_script_step("environment.project_prepare").script_path == script.resolve()
    assert "target" not in project.to_document()


def test_legacy_environment_script_is_never_executed_outside_the_plan(tmp_path):
    script = tmp_path / "legacy.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path = tmp_path / "project.yaml"
    path.write_text(
        """version: 1
name: Legacy
repository: components.yaml
runs_dir: runs
environment_script: legacy.sh
""",
        encoding="utf-8",
    )
    project = AuthoringProject.load(path)
    with pytest.raises(ProjectError, match="obsolete.*script step"):
        project.prepare_environment()


def test_application_ownership_remains_object_local():
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
