"""Test Plan run lifecycle that restores the window which launched the plan."""
from __future__ import annotations

from automation_harness.authoring.gui.plan_visual_window import VisualTestPlanWindow


class LaunchRestoringTestPlanWindow(VisualTestPlanWindow):
    """Return the authoring context to the foreground when execution completes."""

    def _run_finished(self, result, error):
        launching_window = getattr(self, "launching_window", None)
        if launching_window is not None:
            try:
                launching_window.deiconify()
                launching_window.present()
            except Exception:
                # The launcher may have been closed while the test was running.
                # That must not interfere with completion handling for the Plan.
                pass
        return super()._run_finished(result, error)
