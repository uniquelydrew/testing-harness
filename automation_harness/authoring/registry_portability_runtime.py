"""Portable reusable-step snapshots for the live authoring surface."""
from __future__ import annotations

from automation_harness.core.reusable_step_snapshot import (
    load_snapshotted_reusable_steps,
    snapshot_reusable_dependencies,
)
from automation_harness.core.test_plan import save_plan


def install(app_module):
    if getattr(app_module, "_authoring_registry_portability_installed", False):
        return

    original_save_plan = app_module.AuthoringApp.save_plan_dialog
    original_open_plan = app_module.AuthoringApp.open_plan_dialog

    def save_plan_dialog(self):
        result = original_save_plan(self)
        if self.plan_path is None:
            return result
        definitions = dict(getattr(self, "registry_step_definitions", {}) or {})
        if not definitions:
            return result
        try:
            effective_repository = self.repository
            resources = getattr(self, "registry_resources", None)
            if resources is not None and resources.repository.components:
                from automation_harness.authoring.registry_runtime import _effective_repository
                effective_repository = _effective_repository(self)
            self.plan = snapshot_reusable_dependencies(
                self.plan,
                definitions,
                effective_repository,
            )
            save_plan(self.plan, self.plan_path)
        except Exception as exc:
            return self._error(
                "Save Test Plan",
                "Plan was saved, but reusable dependencies could not be snapshotted: %s: %s" %
                (type(exc).__name__, exc),
            )
        self._set_status("Saved portable plan with reusable Step snapshots: %s" % self.plan_path)
        return result

    def open_plan_dialog(self):
        result = original_open_plan(self)
        try:
            snapshots = load_snapshotted_reusable_steps(self.plan)
            if snapshots:
                merged = dict(getattr(self, "registry_step_definitions", {}) or {})
                for step_id, definition in snapshots.items():
                    existing = merged.get(step_id)
                    if existing is not None and existing != definition:
                        # The plan snapshot is authoritative for standalone reproducibility.
                        merged[step_id] = definition
                    else:
                        merged[step_id] = definition
                self.registry_step_definitions = merged
                if hasattr(self, "refresh_registry_library"):
                    self.refresh_registry_library()
                self._set_status(
                    "Opened plan with %d reusable Step snapshot(s)" % len(snapshots)
                )
        except Exception as exc:
            self._error("Open Test Plan", "Reusable Step snapshot error: %s" % exc)
        return result

    app_module.AuthoringApp.save_plan_dialog = save_plan_dialog
    app_module.AuthoringApp.open_plan_dialog = open_plan_dialog
    app_module._authoring_registry_portability_installed = True
