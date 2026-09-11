"""Project-scoped workflows for reusable Step Registry authoring."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from automation_harness.authoring.project import AuthoringProject, save_authoring_project
from automation_harness.authoring.reusable_extraction import save_plan_selection_to_registry
from automation_harness.authoring.step_registry import AuthoringStepRegistry
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.models.plan import TestPlan


def save_plan_selection_to_project_registry(
    project_path: Path,
    plan: TestPlan,
    *,
    source_repository: ComponentRepository,
    registry_path: Path,
    step_id: str,
    name: str,
    node_ids: Iterable[str] | None = None,
    group: str | None = None,
    description: str = "",
    create_registry_name: str | None = None,
    repository: Path | None = None,
) -> tuple[AuthoringProject, AuthoringStepRegistry]:
    """Save a reusable composition and ensure its artifacts belong to the Project.

    The Step Registry and its mandatory Object Repository are both added to Project
    membership. Existing membership is idempotent and no child artifact is deleted or
    rewritten merely because membership changes.
    """
    project_path = project_path.resolve()
    project = AuthoringProject.load(project_path)
    registry = save_plan_selection_to_registry(
        plan,
        source_repository=source_repository,
        registry_path=registry_path,
        step_id=step_id,
        name=name,
        node_ids=node_ids,
        group=group,
        description=description,
        create_registry_name=create_registry_name,
        repository=repository,
    )
    updated = (
        project
        .with_step_registry(registry_path)
        .with_object_repository(registry.repository)
    )
    save_authoring_project(project_path, updated)
    return updated, registry
