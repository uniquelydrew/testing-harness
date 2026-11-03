"""Execution-aware desktop window observation and orchestration.

The monitor observes external window state; the orchestrator enforces action
preconditions. Neither owns semantic object actions or GTK widgets.
"""
from automation_harness.core.window_orchestration.monitor import (
    WindowMonitor, WindowSnapshot, WindowDescriptor, WindowLifecycleEvent,
)
from automation_harness.core.window_orchestration.orchestrator import (
    WindowOrchestrator, WindowOrchestrationError,
)
__all__ = (
    "WindowMonitor", "WindowSnapshot", "WindowDescriptor", "WindowLifecycleEvent",
    "WindowOrchestrator", "WindowOrchestrationError",
)
