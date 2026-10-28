"""Shared plan resource resolution for Project Manager and external adapters."""

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from automation_harness.authoring.plan_repository import assigned_repositories, load_repository_set
from automation_harness.authoring.step_registry import load_step_registry_resources
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.reusable_step_snapshot import load_snapshotted_reusable_steps
from automation_harness.core.reusable_steps import ReusableStepDefinition
from automation_harness.core.repository_preparation import RepositoryResolver, compose_canonical_repositories
from automation_harness.models.plan import TestPlan


@dataclass(frozen=True)
class ResolvedPlanResources:
    repository: ComponentRepository
    reusable_steps: Mapping[str, ReusableStepDefinition]


def resolve_plan_resources(plan: TestPlan, plan_path: Path, project=None) -> ResolvedPlanResources:
    """Resolve the same object and reusable-step inputs as project batch execution.

    Explicit plan repository associations are path-relative to the plan file.
    A project's step registries override portable reusable-step snapshots,
    matching the existing Project Manager execution contract.
    """
    plan_path = Path(plan_path).resolve()
    resources = (
        load_step_registry_resources(project.step_registries)
        if project is not None and project.step_registries else None
    )
    reusable = dict(resources.steps) if resources is not None else load_snapshotted_reusable_steps(plan)
    canonical = load_repository_set(plan, plan_path).compose() if assigned_repositories(plan, plan_path) else None
    if resources is not None:
        canonical = compose_canonical_repositories(
            (resources.repository,) if canonical is None else (canonical, resources.repository)
        )
    # This layer discovers available canonical inputs.  The execution boundary
    # applies the plan's linked/portable/locked preference so qualification
    # failures are reported uniformly as run validation errors.
    prepared = RepositoryResolver().resolve(
        plan, canonical_repository=canonical,
        mode="canonical" if plan.execution_mode == "canonical" else "linked",
    )
    return ResolvedPlanResources(repository=prepared.repository, reusable_steps=reusable)
