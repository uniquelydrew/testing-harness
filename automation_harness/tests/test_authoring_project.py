from dataclasses import fields

import yaml

import pytest

from automation_harness.authoring.project import AuthoringProject, ProjectError, create_authoring_project
from automation_harness.core.script_steps import registered_script_step


def test_project_round_trip_contains_only_authoring_resources(tmp_path):
    path = tmp_path / "project.ahproject"
    created = create_authoring_project(path, "Authoring smoke test")
    loaded = AuthoringProject.load(path)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert loaded.name == created.name
    assert loaded.repository.is_file()
    assert {item.name for item in fields(AuthoringProject)} == {
        "name", "root", "repository", "runs_dir", "script_steps"
    }
    assert document == {
        "version": 1,
        "name": "Authoring smoke test",
        "repository": "objects.ahobjects",
        "runs_dir": "runs",
    }


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
""",
        encoding="utf-8",
    )

    project = AuthoringProject.load(path)

    assert project.repository == (tmp_path / "assets/components.yaml").resolve()
    assert project.runs_dir == (tmp_path / "evidence").resolve()
    assert project.script_steps == (manifest.resolve(),)
    assert registered_script_step("environment.project_prepare").script_path == script.resolve()
    assert project.to_document()["script_steps"] == ["scripts/prepare.yaml"]


def test_project_rejects_missing_script_step_manifest(tmp_path):
    path = tmp_path / "project.yaml"
    path.write_text(
        """version: 1
name: Example
script_steps:
  - scripts/missing.yaml
""",
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match="script-step manifest does not exist"):
        AuthoringProject.load(path)


@pytest.mark.parametrize("obsolete", ["target", "environment_script"])
def test_project_rejects_obsolete_execution_scope_fields(tmp_path, obsolete):
    path = tmp_path / "project.yaml"
    value = "{}" if obsolete == "target" else "legacy.sh"
    path.write_text(
        "version: 1\nname: Legacy\n%s: %s\n" % (obsolete, value),
        encoding="utf-8",
    )

    with pytest.raises(ProjectError, match="obsolete project field"):
        AuthoringProject.load(path)
