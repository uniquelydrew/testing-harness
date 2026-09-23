"""Transactional repository materialization for recorded interactions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Optional

from automation_harness.authoring.plan_repository import materialize_captured_target
from automation_harness.core.component_repository import ComponentRepository
from automation_harness.core.logical_menu import (
    LogicalMenuTarget,
    attach_context_menu_invoker,
    find_logical_menu_targets,
    logical_menu_metadata,
    logical_menu_target_is_persisted,
    stage_recorded_menu_capture,
)
from automation_harness.core.menu_navigation import navigation_from_path
from automation_harness.recording.session import RecordedInteraction, RepositoryMatch


@dataclass(frozen=True)
class RecordingReviewMaterialization:
    repository: ComponentRepository
    interaction: RecordedInteraction
    changed_component_ids: tuple[str, ...] = ()
    created_component_ids: tuple[str, ...] = ()
    provisional_component_ids: tuple[str, ...] = ()
    inventory_changed: bool = False
    error: Optional[Exception] = None

    @property
    def resolved(self) -> bool:
        return self.error is None and self.interaction.repository_match.component_id is not None


def materialize_recorded_interaction(
    repository: ComponentRepository,
    interaction: RecordedInteraction,
    *,
    recording_repository: ComponentRepository | None = None,
) -> RecordingReviewMaterialization:
    """Materialize one recorded interaction without leaking partial changes.

    Non-provisional failures return the original repository unchanged.
    Provisional runtime-only definitions are returned in the working repository
    for review, but the interaction still carries an error and therefore cannot
    become an executable step. Successful results retain readable aliases and
    structured menu subobject paths.
    """
    original = repository
    reviewed = interaction
    provisional = []
    inventory_changed = False

    try:
        evidence = dict(reviewed.evidence or {})
        if reviewed.confidence <= 0.0:
            raise ValueError("recorded interaction has zero confidence and cannot be materialized")
        if evidence.get("menu_route_error"):
            raise ValueError(
                "failed menu route cannot be materialized as an ordinary interaction: %s"
                % evidence.get("menu_route_error")
            )

        if reviewed.target is None:
            if reviewed.repository_match.component_id is None:
                raise ValueError("recorded interaction has no semantic target")
            return _finish(original, repository, reviewed)

        menu_owner_capture = evidence.get("menu_owner_capture")
        menu_invoking_capture = evidence.get("menu_invoking_capture")

        existing_target = _menu_target(reviewed)
        existing_persisted = bool(
            existing_target is not None
            and logical_menu_target_is_persisted(repository, existing_target)
        )

        if menu_owner_capture is not None and not existing_persisted:
            repository, owner_id, _created = materialize_captured_target(
                repository, menu_owner_capture,
            )
            owner = repository.get(owner_id)
            if _is_provisional(owner):
                provisional.append(owner.component_id)
                raise ValueError(
                    "recorded menu owner %r has only provisional runtime identity"
                    % owner.component_id
                )

            matches = find_logical_menu_targets((owner,), reviewed.target)
            if len(matches) == 1:
                target = matches[0]
                reviewed = replace(
                    reviewed,
                    repository_match=RepositoryMatch(
                        "known_subobject",
                        (target.owner_component_id,),
                        target.subobject_path,
                    ),
                )
            elif logical_menu_metadata(reviewed.target) is None:
                raise ValueError(
                    "recorded menu terminal could not be matched to the captured menu inventory"
                )

        existing_target = _menu_target(reviewed)
        if (
            existing_target is not None
            and existing_target.owner_component_id not in repository.components
        ):
            source = recording_repository
            if (
                source is None
                or existing_target.owner_component_id not in source.components
            ):
                raise ValueError(
                    "logical menu owner %r is not available in the authoring repository"
                    % existing_target.owner_component_id
                )
            owner = source.get(existing_target.owner_component_id)
            if owner.owner_object_id is not None:
                owner = replace(owner, owner_object_id=None)
            repository = repository.with_component(owner)

        metadata = logical_menu_metadata(reviewed.target)
        if metadata is not None:
            needs_staging = (
                existing_target is None
                or not logical_menu_target_is_persisted(repository, existing_target)
            )
            if needs_staging:
                repository, target, _owner_created, changed = (
                    stage_recorded_menu_capture(repository, reviewed.target)
                )
                if target is None:
                    raise ValueError(
                        "recorded menu interaction could not be attached to a unique logical menu owner"
                    )
                inventory_changed = inventory_changed or changed
                reviewed = replace(
                    reviewed,
                    repository_match=RepositoryMatch(
                        "known_subobject",
                        (target.owner_component_id,),
                        target.subobject_path,
                    ),
                )

        if (
            reviewed.repository_match.component_id is None
            and reviewed.repository_match.status in {"new_candidate", "unresolved"}
        ):
            repository, component_id, _created = materialize_captured_target(
                repository, reviewed.target,
            )
            definition = repository.get(component_id)
            if _is_provisional(definition):
                provisional.append(definition.component_id)
                raise ValueError(
                    "recorded object %r is provisional and cannot become an executable step"
                    % definition.component_id
                )
            reviewed = replace(
                reviewed,
                repository_match=RepositoryMatch(
                    "known_unique", (definition.component_id,),
                ),
            )

        if reviewed.repository_match.component_id is None:
            raise ValueError(
                "recorded interaction has no unique repository component"
            )

        if (
            menu_invoking_capture is not None
            and reviewed.repository_match.status == "known_subobject"
        ):
            menu_id = reviewed.repository_match.component_id
            if menu_id not in repository.components:
                raise ValueError(
                    "context menu owner %r is unavailable" % menu_id
                )
            repository, invoker_id, _created = materialize_captured_target(
                repository, menu_invoking_capture,
            )
            invoker = repository.get(invoker_id)
            if _is_provisional(invoker):
                provisional.append(invoker.component_id)
                raise ValueError(
                    "context-menu invoking object %r is provisional"
                    % invoker.component_id
                )
            repository, _changed = attach_context_menu_invoker(
                repository, menu_id, invoker_id,
            )

        if reviewed.repository_match.status == "known_subobject":
            target = _menu_target(reviewed)
            if target is None or target.owner_component_id not in repository.components:
                raise ValueError("recorded menu selection has no persisted owner")
            owner = repository.get(target.owner_component_id)
            navigation = navigation_from_path(
                owner.subobjects, target.subobject_path,
            )
            reviewed = replace(
                reviewed,
                evidence={
                    **dict(reviewed.evidence or {}),
                    "menu_navigation": navigation,
                },
            )

        return _finish(
            original,
            repository,
            reviewed,
            provisional=tuple(provisional),
            inventory_changed=inventory_changed,
        )
    except Exception as exc:
        # A runtime-only capture is useful authoring evidence even though it is
        # deliberately not executable. Preserve that candidate for the review
        # UI; all other failures retain the transaction's rollback guarantee.
        retained_repository = repository if provisional else original
        changed, created = _component_changes(original, retained_repository)
        return RecordingReviewMaterialization(
            repository=retained_repository,
            interaction=interaction,
            changed_component_ids=tuple(sorted(changed)),
            created_component_ids=tuple(sorted(created)),
            provisional_component_ids=tuple(provisional),
            error=exc,
        )


def _menu_target(interaction):
    match = interaction.repository_match
    if (
        match.status == "known_subobject"
        and match.component_id is not None
        and match.subobject_path
    ):
        return LogicalMenuTarget(
            match.component_id,
            match.subobject_path,
            (),
        )
    return None


def _is_provisional(definition) -> bool:
    return str(
        dict(definition.properties or {}).get("locator_status") or "ready"
    ) == "provisional"


def _finish(
    original,
    repository,
    interaction,
    *,
    provisional=(),
    inventory_changed=False,
):
    # Recording review is the repository commit boundary. Re-parse the exact
    # serialized form so malformed captures cannot poison the repository file.
    repository.validate_persistence()
    changed, created = _component_changes(original, repository)
    return RecordingReviewMaterialization(
        repository=repository,
        interaction=interaction,
        changed_component_ids=tuple(sorted(changed)),
        created_component_ids=tuple(sorted(created)),
        provisional_component_ids=tuple(provisional),
        inventory_changed=bool(inventory_changed),
    )


def _component_changes(original, repository):
    before = {
        item.object_id: (item.component_id, item.revision, item.owner_object_id)
        for item in original.components.values()
    }
    changed = []
    created = []
    for item in repository.components.values():
        previous = before.get(item.object_id)
        current = (item.component_id, item.revision, item.owner_object_id)
        if previous is None:
            created.append(item.component_id)
            changed.append(item.component_id)
        elif previous != current:
            changed.append(item.component_id)
    return changed, created
