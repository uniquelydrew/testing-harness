from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from automation_harness.models.run import BackendHealth


class ExecutionBackend(ABC):
    name: str

    @property
    @abstractmethod
    def capabilities(self) -> set[str]:
        raise NotImplementedError

    @property
    def allowed_step_risks(self) -> frozenset[str]:
        """Step risk classes this backend permits in declarative execution."""
        return frozenset({"read_only"})

    def preflight_issues(self) -> list[str]:
        """Return host/environment blockers without starting the target."""
        return []

    @abstractmethod
    def start(self, *, run_dir: Path) -> dict[str, str]:
        """Start the backend and return environment variables required by tests."""
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> BackendHealth:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError
