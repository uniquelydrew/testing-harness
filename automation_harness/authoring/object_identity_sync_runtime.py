"""Authoring integration for stable object identity across capture and rename."""
from __future__ import annotations

from dataclasses import replace

from automation_harness.core.object_identity_sync import (
    find_existing_component_ids,
    rename_plan_component,
    rename_repository_component,
)

_INSTALLED = False


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    from automation_harness.authoring.object_identity_workbench import ObjectIdentityWorkbench
    from automation_harness.authoring import plan_repository
    from automation_harness.recording.session import RecordingSession, RepositoryMatch

    original_save_node = ObjectIdentityWorkbench._save_node
    original_match = RecordingSession._match

    def save_node(self, node, component_id, identity):
        captured = self._captured_for_node(node)
        repository = self.app.repository
        explicit_old = getattr(self.app, "_workbench_original_component_id", None)
        if explicit_old not in repository.components:
            explicit_old = None

        matches = find_existing_component_ids(repository, captured)
        old_component_id = explicit_old
        if old_component_id is None:
            if len(matches) > 1:
                raise ValueError(
                    "capture matches multiple existing objects (%s); refine identity before saving"
                    % ", ".join(matches)
                )
            if len(matches) == 1:
                old_component_id = matches[0]

        # A typed name that already belongs to a different live identity must
        # never silently overwrite that repository object.
        if component_id in repository.components and old_component_id not in {None, component_id}:
            raise ValueError(
                "object name %r already belongs to a different repository component" % component_id
            )

        renamed_from = None
        if old_component_id is not None and old_component_id != component_id:
            self.app.repository = rename_repository_component(
                repository, old_component_id, component_id,
            )
            renamed_from = old_component_id
            _propagate_app_rename(self.app, old_component_id, component_id)

        result = original_save_node(self, node, component_id, identity)

        # Keep edit sessions bound to immutable object identity after the first
        # rename so subsequent Save Selected operations update instead of clone.
        if hasattr(self.app, "_workbench_original_component_id"):
            self.app._workbench_original_component_id = component_id
            try:
                self.app._workbench_original_definition = self.app.repository.get(component_id)
            except Exception:
                pass

        if renamed_from is not None:
            _refresh_after_rename(self.app, component_id)
        return result

    def match(self, target):
        result = original_match(self, target)
        if target is None or self.repository is None or result.status != "new_candidate":
            return result
        matches = find_existing_component_ids(self.repository, target)
        if len(matches) == 1:
            return RepositoryMatch("known_unique", matches)
        if len(matches) > 1:
            return RepositoryMatch("ambiguous", matches)
        return result

    def matching_component_ids(repository, capture):
        return list(find_existing_component_ids(repository, capture))

    ObjectIdentityWorkbench._save_node = save_node
    RecordingSession._match = match
    # Every plan-authoring capture materialization checks semantic identity
    # before generating a name. This covers direct recording/capture paths that
    # never open Object Identity Workbench.
    plan_repository.matching_component_ids = matching_component_ids


def _propagate_app_rename(app, old_component_id: str, new_component_id: str) -> None:
    plan = getattr(app, "plan", None)
    if plan is not None:
        app.plan = rename_plan_component(plan, old_component_id, new_component_id)

    interactions = getattr(app, "recorded_interactions", None)
    if isinstance(interactions, list):
        updated = []
        for interaction in interactions:
            match = getattr(interaction, "repository_match", None)
            ids = tuple(getattr(match, "component_ids", ()) or ())
            if old_component_id in ids:
                new_ids = tuple(new_component_id if item == old_component_id else item for item in ids)
                interaction = replace(
                    interaction,
                    repository_match=replace(match, component_ids=new_ids),
                )
            updated.append(interaction)
        app.recorded_interactions = updated

    # Preserve transient selection state so refreshes point at the new name.
    if getattr(app, "_workbench_original_component_id", None) == old_component_id:
        app._workbench_original_component_id = new_component_id


def _refresh_after_rename(app, component_id: str) -> None:
    refresh_all = getattr(app, "refresh_all", None)
    if callable(refresh_all):
        refresh_all()
    else:
        refresh_objects = getattr(app, "refresh_objects", None)
        if callable(refresh_objects):
            refresh_objects()
        refresh = getattr(app, "refresh", None)
        if callable(refresh):
            refresh()

    step_object = getattr(app, "step_object", None)
    if step_object is not None and hasattr(step_object, "set_active_id"):
        step_object.set_active_id(component_id)

    object_tree = getattr(app, "object_tree", None)
    select_value = getattr(app, "_select_value", None)
    if object_tree is not None and callable(select_value):
        select_value(object_tree, component_id)

    # Standalone repository workbench uses its own hierarchical tree model.
    find_iter = getattr(app, "_find_component_iter", None)
    tree = getattr(app, "tree", None)
    if callable(find_iter) and tree is not None:
        iterator = find_iter(component_id)
        if iterator is not None:
            tree.get_selection().select_iter(iterator)
