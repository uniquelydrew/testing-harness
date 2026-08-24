from __future__ import annotations

from pathlib import Path

from automation_harness.backends.base import ExecutionBackend
from automation_harness.models.run import BackendHealth


class ProtectedBackend(ExecutionBackend):
    """Protected-system boundary.

    Intentionally non-runnable until environment-specific adapters are implemented
    and explicitly enabled inside the protected environment.
    """

    name = "protected"

    @property
    def allowed_step_risks(self) -> frozenset[str]:
        return frozenset()

    @property
    def capabilities(self) -> set[str]:
        return set()

    def preflight_issues(self) -> list[str]:
        return [
            "protected backend is intentionally disabled in this development build; "
            "only the synthetic reference backend may execute"
        ]

    def start(self, *, run_dir: Path) -> dict[str, str]:
        raise RuntimeError(
            "Protected backend is intentionally disabled in the development harness. "
            "Use --backend reference for pre-protected-system validation."
        )

    def health_check(self) -> BackendHealth:
        return BackendHealth(False, self.name, {"reason": "protected backend disabled"})

    def stop(self) -> None:
        return None
