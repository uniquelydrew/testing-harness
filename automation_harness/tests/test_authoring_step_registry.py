import yaml
import pytest

from automation_harness.authoring.step_registry import (
    AuthoringStepRegistry,
    StepRegistryArtifactError,
    create_step_registry,
    save_step_registry,
)
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.models.plan import TestPlan


def test_create_registry_creates_empty_repository_by_default(tmp_path):
    requested = tmp_path / "common"

    registry = create_step_registry(requested, "Common Steps")
    registry_path = tmp_path / "common.ahregistry"
    repository_path = tmp_path / "common.ahobjects"

    assert registry_path.is_file()
    assert repository_path.is_file()
    assert registry.repository == repository_path.resolve()
    assert yaml.safe_load(repository_path.read_text(encoding="utf-8")) == {
        "version": 3,
        "components": {},
    }


def test_registry_round_trip_embeds_reusable_compositions(tmp_path):
    path = tmp_path / "common.ahregistry"
    registry = create_step_registry(path, "Common Steps")
    step = ReusableStepDefinition(
        step_id="authentication.login",
        name="Login",
        description="Authenticate a user",
        plan=TestPlan(name="Login composition"),
        inputs={"username": {"type": "str", "required": True}},
        outputs={"authenticated_user": "current_user"},
    )

    registry = registry.with_step(step)
    save_step_registry(path, registry)
    loaded = AuthoringStepRegistry.load(path)

    assert loaded.name == "Common Steps"
    assert loaded.repository == registry.repository
    assert len(loaded.steps) == 1
    assert loaded.steps[0].step_id == "authentication.login"
    assert loaded.steps[0].plan.name == "Login composition"
    assert loaded.steps[0].inputs == step.inputs
    assert loaded.steps[0].outputs == step.outputs


def test_registry_can_use_existing_repository(tmp_path):
    repository = tmp_path / "shared.ahobjects"
    repository.write_text("version: 3\ncomponents: {}\n", encoding="utf-8")

    registry = create_step_registry(
        tmp_path / "common.ahregistry",
        "Common Steps",
        repository=repository,
    )

    assert registry.repository == repository.resolve()


def test_registry_requires_repository_to_exist(tmp_path):
    path = tmp_path / "broken.ahregistry"
    path.write_text(
        """version: 1
name: Broken
object_repository: missing.ahobjects
steps: []
""",
        encoding="utf-8",
    )

    with pytest.raises(StepRegistryArtifactError, match="does not exist"):
        AuthoringStepRegistry.load(path)


def test_registry_step_ids_are_unique(tmp_path):
    path = tmp_path / "common.ahregistry"
    registry = create_step_registry(path, "Common Steps")
    step = ReusableStepDefinition(
        step_id="navigation.open",
        name="Open",
        description="",
        plan=TestPlan(name="Open"),
        inputs={},
        outputs={},
    )
    registry = registry.with_step(step).with_step(step)
    save_step_registry(path, registry)

    loaded = AuthoringStepRegistry.load(path)
    assert [item.step_id for item in loaded.steps] == ["navigation.open"]
