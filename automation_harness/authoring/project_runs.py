"""Project-scoped batch execution and durable, external run history."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

from automation_harness.authoring.plan_repository import assigned_repositories, load_repository_set
from automation_harness.authoring.step_registry import load_step_registry_resources
from automation_harness.backends.live_desktop import LiveDesktopBackend
from automation_harness.core.reusable_step_snapshot import load_snapshotted_reusable_steps
from automation_harness.core.test_plan import load_plan, repository_from_plan
from automation_harness.runner.plan_execution import execute_plan


_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ProjectPlanRun:
    plan_path: str
    plan_name: str
    status: str
    started_at: str
    finished_at: str | None = None
    passed: int = 0
    failed: int = 0
    exit_code: int | None = None
    artifact_dir: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "plan_path": self.plan_path, "plan_name": self.plan_name, "status": self.status,
            "started_at": self.started_at, "finished_at": self.finished_at, "passed": self.passed,
            "failed": self.failed, "exit_code": self.exit_code, "artifact_dir": self.artifact_dir,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, raw: Mapping) -> "ProjectPlanRun":
        required = ("plan_path", "plan_name", "status", "started_at")
        if not all(isinstance(raw.get(key), str) and raw[key] for key in required):
            raise ValueError("invalid project plan run")
        return cls(
            plan_path=raw["plan_path"], plan_name=raw["plan_name"], status=raw["status"],
            started_at=raw["started_at"], finished_at=raw.get("finished_at"),
            passed=int(raw.get("passed", 0)), failed=int(raw.get("failed", 0)),
            exit_code=raw.get("exit_code"), artifact_dir=raw.get("artifact_dir"), error=raw.get("error"),
        )


@dataclass(frozen=True)
class ProjectBatchRun:
    batch_id: str
    project_path: str
    started_at: str
    plans: tuple[ProjectPlanRun, ...] = ()
    finished_at: str | None = None

    @property
    def passed(self) -> int:
        return sum(1 for item in self.plans if item.status == "passed")

    @property
    def failed(self) -> int:
        return sum(1 for item in self.plans if item.status != "passed")

    @property
    def status(self) -> str:
        if self.finished_at is None:
            return "running"
        return "passed" if not self.failed else "failed"

    def to_dict(self) -> dict:
        return {
            "version": _VERSION, "batch_id": self.batch_id, "project_path": self.project_path,
            "started_at": self.started_at, "finished_at": self.finished_at,
            "plans": [item.to_dict() for item in self.plans],
        }

    @classmethod
    def from_dict(cls, raw: Mapping) -> "ProjectBatchRun":
        if raw.get("version") != _VERSION:
            raise ValueError("unsupported project batch version")
        if not all(isinstance(raw.get(key), str) and raw[key] for key in ("batch_id", "project_path", "started_at")):
            raise ValueError("invalid project batch")
        plans = raw.get("plans", [])
        if not isinstance(plans, list):
            raise ValueError("project batch plans must be a list")
        return cls(raw["batch_id"], raw["project_path"], raw["started_at"], tuple(ProjectPlanRun.from_dict(item) for item in plans), raw.get("finished_at"))


def history_directory(runs_dir: Path, project_path: Path) -> Path:
    identity = hashlib.sha256(str(Path(project_path).resolve()).encode("utf-8")).hexdigest()[:20]
    return Path(runs_dir).resolve() / "project-batches" / identity


def save_batch(runs_dir: Path, batch: ProjectBatchRun) -> Path:
    directory = history_directory(runs_dir, Path(batch.project_path))
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (batch.batch_id + ".json")
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(batch.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def load_batches(runs_dir: Path, project_path: Path) -> tuple[ProjectBatchRun, ...]:
    directory = history_directory(runs_dir, project_path)
    if not directory.is_dir():
        return ()
    result = []
    for path in directory.glob("*.json"):
        try:
            batch = ProjectBatchRun.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if Path(batch.project_path).resolve() == Path(project_path).resolve():
                result.append(batch)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return tuple(sorted(result, key=lambda item: item.started_at, reverse=True))


ProgressCallback = Callable[[str, int, int, Optional[ProjectPlanRun]], None]


def execute_project_batch(project, project_path: Path, plan_paths: Iterable[Path], *, runs_dir: Path,
                          progress: ProgressCallback | None = None,
                          backend_factory=LiveDesktopBackend) -> ProjectBatchRun:
    """Run saved Project plans in order, retaining failures and continuing onward."""
    selected = tuple(Path(path).resolve() for path in plan_paths)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    batch = ProjectBatchRun(stamp, str(Path(project_path).resolve()), _now())
    completed: list[ProjectPlanRun] = []
    total = len(selected)
    save_batch(runs_dir, batch)
    try:
        for index, plan_path in enumerate(selected, 1):
            started = _now()
            pending = ProjectPlanRun(str(plan_path), plan_path.stem, "running", started)
            if progress:
                progress("started", index, total, pending)
            try:
                plan = load_plan(plan_path)
                resources = load_step_registry_resources(project.step_registries) if project.step_registries else None
                reusable = dict(resources.steps) if resources else load_snapshotted_reusable_steps(plan)
                repository = repository_from_plan(plan)
                if assigned_repositories(plan, plan_path):
                    repository = repository.overlay(load_repository_set(plan, plan_path).compose())
                if resources:
                    repository = repository.overlay(resources.repository)
                result = execute_plan(plan, backend_factory(), runs_dir=Path(runs_dir), component_repository=repository, reusable_steps=reusable)
                record = ProjectPlanRun(
                    str(plan_path), plan.name, "passed" if result.exit_code == 0 else "failed", started, _now(),
                    result.passed, result.failed, result.exit_code,
                    str(result.artifact_dir) if result.artifact_dir else None,
                    "\n".join(result.validation_errors) or None,
                )
            except Exception as exc:
                record = ProjectPlanRun(str(plan_path), plan_path.stem, "failed", started, _now(), error="%s: %s" % (type(exc).__name__, exc), exit_code=2)
            completed.append(record)
            batch = ProjectBatchRun(batch.batch_id, batch.project_path, batch.started_at, tuple(completed))
            save_batch(runs_dir, batch)
            if progress:
                progress("finished", index, total, record)
    finally:
        batch = ProjectBatchRun(batch.batch_id, batch.project_path, batch.started_at, tuple(completed), _now())
        save_batch(runs_dir, batch)
    return batch


def failed_steps(record: ProjectPlanRun) -> tuple[dict, ...]:
    """Return failed step and assertion evidence from a plan's authoritative events."""
    if not record.artifact_dir:
        return ()
    events_path = Path(record.artifact_dir) / "events.jsonl"
    if not events_path.is_file():
        return ()
    result = []
    try:
        assertions = {}
        failures = []
        for line in events_path.read_text(encoding="utf-8").splitlines():
            event = json.loads(line)
            if event.get("event") == "assertion" and event.get("passed") is False:
                assertions.setdefault(event.get("node_id"), []).append(event)
            elif event.get("event") == "plan_step_failed":
                failures.append(event)
        for event in failures:
            node_id = event.get("node_id", "")
            result.append({"node_id": node_id, "step": event.get("step", ""), "error": event.get("error", ""), "assertions": assertions.get(node_id, [])})
        state_path = Path(record.artifact_dir) / "execution_state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            for node_id, node in state.get("steps", {}).items():
                if node.get("status") == "failed" and not any(item["node_id"] == node_id for item in result):
                    result.append({"node_id": node_id, "step": node.get("step_id", ""), "error": node.get("error", ""), "assertions": assertions.get(node_id, [])})
    except (OSError, ValueError, json.JSONDecodeError):
        return ()
    return tuple(result)
