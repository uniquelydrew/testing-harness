"""Reusable domain steps.

Step implementations are registered under stable semantic IDs. Existing direct
imports remain supported, while registry invocation allows tests and future
keyword/data-driven runners to reuse the same implementation by name.
"""

from automation_harness.core.step_registry import default_step_registry, invoke_step, registered_step, step
from automation_harness.steps import visual_assert_steps as _visual_assert_steps  # register built-in visual assertions

__all__ = ["default_step_registry", "invoke_step", "registered_step", "step"]
